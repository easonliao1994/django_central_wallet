from django.shortcuts import render
from rest_framework import viewsets, permissions, status, generics, mixins
from django.contrib.auth import get_user_model
from django_central_wallet.utils.paginations import CustomPagination
from .serializers import *
from django.shortcuts import get_object_or_404

class IsAdminOrReadOnly(permissions.BasePermission):
    """Authenticated users can read; only admins can create, update or delete."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        return request.method in permissions.SAFE_METHODS or request.user.is_staff


class BlockchainViewSet(viewsets.ModelViewSet):
    queryset = Blockchain.objects.order_by('id')  # stable pagination
    serializer_class = BlockchainSerializer
    permission_classes = [IsAdminOrReadOnly]
    pagination_class = CustomPagination


class BlockchainDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = BlockchainSerializer
    permission_classes = [IsAdminOrReadOnly]

    def get_object(self):
        pk = self.kwargs["pk"]
        obj = get_object_or_404(Blockchain, id=pk)
        self.check_object_permissions(self.request, obj)
        return obj


class DepositAddressView(generics.RetrieveAPIView):
    """The authenticated user's EVM deposit address (shared by all EVM chains), created on first use."""
    serializer_class = DepositAddressSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        from rest_framework.exceptions import PermissionDenied
        from .wallet import get_or_create_deposit_address

        if not self.request.user.is_verified:
            raise PermissionDenied('Verify your email before requesting a deposit address.')
        return get_or_create_deposit_address(self.request.user)


class WithdrawalViewSet(mixins.CreateModelMixin, mixins.ListModelMixin,
                        mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Create and track withdrawals. Users only see their own."""
    serializer_class = WithdrawalSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = CustomPagination

    def get_queryset(self):
        qs = Withdrawal.objects.select_related('coin_info__blockchain', 'coin_info__coin')
        if getattr(self, 'swagger_fake_view', False):
            return qs.none()
        return qs.filter(user=self.request.user)

    def get_serializer_class(self):
        if self.action == 'create':
            return WithdrawalCreateSerializer
        return super().get_serializer_class()

    def create(self, request, *args, **kwargs):
        """Needs a 2FA code once 2FA is on (and 2FA itself when ``REQUIRE_2FA_FOR_WITHDRAWAL`` is set); with
        whitelist mode on, ``to_address`` must be an active saved address.

        A withdrawal to another user's deposit address that would be auto-approved becomes an internal
        transfer: instant and free instead of paying gas between two platform addresses. Anything that
        needs review stays a normal withdrawal, so routing never bypasses the withdrawal controls."""
        from django.db import transaction
        from rest_framework import status as http_status
        from rest_framework.response import Response

        from account.two_factor import authorize_outgoing

        from .transfers import platform_address, transfer
        from .withdrawals import parse_amount, would_auto_approve

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        coin_info = data['coin_info']
        with transaction.atomic():  # a failed withdrawal does not use up the 2FA code
            allowed = authorize_outgoing(request.user, data.pop('otp_code', None), to_address=data['to_address'])
            target = platform_address(data['to_address'])
            if target is not None and target.user_id != request.user.pk:
                amount = parse_amount(coin_info, data['amount'])
                if amount >= coin_info.min_withdrawal_amount and would_auto_approve(request.user, coin_info, amount):
                    t = transfer(request.user, coin_info, data['to_address'], amount, allowed_addresses=allowed)
                    _notify_sent(request.user, t)
                    return Response(InternalTransferSerializer(t, context=self.get_serializer_context()).data,
                                    status=http_status.HTTP_201_CREATED)
            w = serializer.save()
            _notify_sent(request.user, w)
        return Response(serializer.data, status=http_status.HTTP_201_CREATED)


# --------------------------------------------------------------------------- balances & history
def _target_users(request):
    """Whose data to show: the caller; superusers may pass ?user=<id> or ?user=all (None = everyone)."""
    from django_central_wallet.utils.custom_exception_handler import InvalidFilterException

    requested = request.query_params.get('user')
    if not requested or not request.user.is_superuser:
        return request.user.pk
    if requested == 'all':
        return None
    if not requested.isdigit():
        raise InvalidFilterException()
    return int(requested)


def _int_param(request, name):
    from django_central_wallet.utils.custom_exception_handler import InvalidFilterException

    value = request.query_params.get(name)
    if value in (None, ''):
        return None
    if not value.isdigit():
        raise InvalidFilterException()
    return int(value)


def _time_param(request, name, end_of_day=False):
    """ISO date or datetime -> (aware datetime, is_bare_date). A bare date means the start of that day,
    or with ``end_of_day`` the start of the next day (an exclusive upper bound)."""
    from datetime import datetime, time, timedelta

    from django.utils import timezone
    from django.utils.dateparse import parse_date, parse_datetime

    from django_central_wallet.utils.custom_exception_handler import InvalidFilterException

    value = request.query_params.get(name)
    if not value:
        return None, False
    try:
        # Dates first: parse_datetime also accepts a bare date (as midnight).
        day = parse_date(value)
        is_date = day is not None
        if is_date:
            parsed = datetime.combine(day + timedelta(days=1) if end_of_day else day, time.min)
        else:
            parsed = parse_datetime(value)
            if parsed is None:
                raise ValueError(value)
    except ValueError:
        raise InvalidFilterException()
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed, is_date


class BalanceListView(generics.GenericAPIView):
    """My balance for every coin on every chain.

    Filters: ``blockchain`` (id), ``symbol``. Superusers: ``user=<id>`` or ``user=all`` (platform totals).
    """
    serializer_class = BalanceSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def get(self, request):
        from django.db.models import Sum

        from django_central_wallet.utils.global_method import format_amount
        from rest_framework.response import Response

        user_id = _target_users(request)
        coin_infos = CoinInfo.objects.filter(coin__isnull=False, blockchain__isnull=False) \
            .select_related('coin', 'blockchain').order_by('blockchain_id', 'id')
        blockchain_id = _int_param(request, 'blockchain')
        if blockchain_id is not None:
            coin_infos = coin_infos.filter(blockchain_id=blockchain_id)
        symbol = request.query_params.get('symbol')
        if symbol:
            coin_infos = coin_infos.filter(coin__symbol__iexact=symbol)

        balances = CoinInfoBalance.objects.filter(coin_info__in=coin_infos)
        if user_id is not None:
            balances = balances.filter(crypto_address__user_id=user_id)
        totals = {row['coin_info']: int(row['internal'] or 0) + int(row['external'] or 0)
                  for row in balances.values('coin_info').annotate(internal=Sum('internal_amount'),
                                                                   external=Sum('external_amount'))}
        rows = []
        for ci in coin_infos:
            raw = totals.get(ci.pk, 0)
            rows.append({
                'coin_info': ci.pk, 'blockchain_id': ci.blockchain_id, 'blockchain': ci.blockchain.name,
                'symbol': ci.coin.symbol, 'name': ci.coin.name, 'contract': ci.contract or None, 'decimal': ci.decimal,
                'balance': format_amount(raw, ci.decimal, ci.coin.scale), 'balance_raw': str(raw),
            })
        return Response(BalanceSerializer(rows, many=True).data)


class TransactionViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
                         viewsets.GenericViewSet):
    """My deposits, withdrawals and internal transfers, newest first.

    Filters: ``blockchain`` (id), ``coin_info`` (id), ``symbol``, ``direction`` (comma separated:
    deposit, withdrawal, internal), ``status``, ``created_after`` / ``created_before`` (ISO date or
    datetime; a bare ``created_before`` date includes that whole day). Superusers: ``user=<id>`` or
    ``user=all``; platform-internal records (sweeps, gas top-ups) are only listed for superusers.
    """
    serializer_class = TransactionSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = CustomPagination
    USER_DIRECTIONS = [TransactionLog.Direction.DEPOSIT, TransactionLog.Direction.WITHDRAWAL,
                       TransactionLog.Direction.INTERNAL]

    def get_queryset(self):
        from django_central_wallet.utils.custom_exception_handler import InvalidFilterException

        qs = TransactionLog.objects.select_related('blockchain', 'coin_info__coin', 'withdrawal',
                                                   'transfer_as_sender', 'transfer_as_recipient') \
            .order_by('-created_at', '-id')
        if getattr(self, 'swagger_fake_view', False):
            return qs.none()
        request = self.request
        user_id = _target_users(request)
        if user_id is not None:
            qs = qs.filter(user_id=user_id)
        if not request.user.is_superuser:
            qs = qs.filter(direction__in=self.USER_DIRECTIONS)
        if self.action != 'list':
            return qs

        for name, field in (('blockchain', 'blockchain_id'), ('coin_info', 'coin_info_id')):
            value = _int_param(request, name)
            if value is not None:
                qs = qs.filter(**{field: value})
        symbol = request.query_params.get('symbol')
        if symbol:
            qs = qs.filter(coin_info__coin__symbol__iexact=symbol)
        direction = request.query_params.get('direction')
        if direction:
            wanted = [d.strip() for d in direction.split(',') if d.strip()]
            if any(d not in TransactionLog.Direction.values for d in wanted):
                raise InvalidFilterException()
            qs = qs.filter(direction__in=wanted)
        status_param = request.query_params.get('status')
        if status_param:
            if status_param not in TransactionLog.Status.values:
                raise InvalidFilterException()
            qs = qs.filter(status=status_param)
        after, _ = _time_param(request, 'created_after')
        before, before_is_date = _time_param(request, 'created_before', end_of_day=True)
        if after is not None:
            qs = qs.filter(created_at__gte=after)
        if before is not None:
            qs = qs.filter(created_at__lt=before) if before_is_date else qs.filter(created_at__lte=before)
        return qs


class InternalTransferViewSet(mixins.CreateModelMixin, mixins.ListModelMixin,
                              mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Send coins to another user off-chain (by email or platform address), and list transfers I sent or received."""
    serializer_class = InternalTransferSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = CustomPagination

    def get_queryset(self):
        from django.db.models import Q
        qs = InternalTransfer.objects.select_related('coin_info__blockchain', 'coin_info__coin')
        if getattr(self, 'swagger_fake_view', False):
            return qs.none()
        user = self.request.user
        return qs.filter(Q(sender=user) | Q(recipient=user))

    def get_serializer_class(self):
        if self.action == 'create':
            return InternalTransferCreateSerializer
        return super().get_serializer_class()

    def create(self, request, *args, **kwargs):
        """Needs a 2FA code once 2FA is on (and 2FA itself when ``REQUIRE_2FA_FOR_WITHDRAWAL`` is set); with
        whitelist mode on, the recipient's deposit address must be an active saved address."""
        from django.db import transaction
        from rest_framework import status as http_status
        from rest_framework.response import Response

        from account.two_factor import authorize_outgoing

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():  # a failed transfer does not use up the 2FA code
            allowed = authorize_outgoing(request.user, serializer.validated_data.pop('otp_code', None))
            t = serializer.save(allowed_addresses=allowed)
            _notify_sent(request.user, t)
        return Response(serializer.data, status=http_status.HTTP_201_CREATED)


def _notify_sent(user, obj):
    """Email the user about every withdrawal and transfer they send (after the transaction commits)."""
    from account.views import notify
    coin = f'{obj.amount_str} {obj.coin_info.coin.symbol} on {obj.coin_info.blockchain.name}'
    if isinstance(obj, Withdrawal):
        notify(user, 'Withdrawal requested', [
            f'A withdrawal of {coin} to {obj.to_address} was requested from your account.',
            'If this was not you, change your password and contact support immediately.'])
    else:
        notify(user, 'Transfer sent', [
            f'You sent {coin} to {obj.recipient_input or "another user"}.',
            'If this was not you, change your password and contact support immediately.'])
