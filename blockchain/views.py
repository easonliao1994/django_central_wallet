from django.shortcuts import render
from rest_framework import viewsets, permissions, status, generics, mixins
from django_auto_prefetching import AutoPrefetchViewSetMixin
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


class BlockchainViewSet(AutoPrefetchViewSetMixin, viewsets.ModelViewSet):
    queryset = Blockchain.objects.all()
    serializer_class = BlockchainSerializer
    permission_classes = [IsAdminOrReadOnly]
    pagination_class = CustomPagination
    
    def get_queryset(self):
        return super().get_queryset()
    

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


class WithdrawalViewSet(AutoPrefetchViewSetMixin, mixins.CreateModelMixin, mixins.ListModelMixin,
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
