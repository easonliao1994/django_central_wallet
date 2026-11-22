from django.shortcuts import render
from rest_framework import viewsets, permissions, status, generics
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from django_central_wallet.utils.global_method import *
from .models import *
from .serializers import *
from .tokens import EmailVerifyTokenGenerator
from django.contrib.auth import get_user_model
from django_central_wallet.utils.paginations import CustomPagination
# Create your views here.

class UserViewSet(viewsets.ModelViewSet):
    queryset = get_user_model().objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = CustomPagination

    def get_queryset(self):
        if self.request.user.is_superuser:
            return super().get_queryset()
        return super().get_queryset().filter(is_active=True)

    def get_serializer_class(self):
        if self.action == 'create':
            return UserRegistrationSerializer
        return super().get_serializer_class()

    def get_permissions(self):
        if self.action == 'create':
            return [permissions.AllowAny()]
        if self.action == 'destroy':
            return {permissions.IsAuthenticated(), permissions.IsAdminUser()}
        return super().get_permissions()
    
    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['request'] = self.request  # 将请求对象添加到上下文中
        return context


def activate(request, uidb64: str, token: str):
    from django.utils.encoding import force_str
    from django.utils.http import urlsafe_base64_decode
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user is not None and EmailVerifyTokenGenerator().check_token(user, token):
        user.activate(verify_method='email')
        return render(request, 'mail_verified.html')
    else:
        return render(request, 'account_not_activated.html')


# --------------------------------------------------------------------------- auth & profile
from django.contrib.auth.tokens import default_token_generator
from django.db import transaction
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from .serializers import _validate_new_password

from django_central_wallet.utils.custom_exception_handler import (
    AccountDisabledException, EmailNotVerifiedException, InvalidCredentialsException, InvalidTokenException,
)


from .throttles import EmailRateThrottle


class AuthThrottleMixin:
    throttle_classes = [ScopedRateThrottle, EmailRateThrottle]
    throttle_scope = 'auth'


def _request_domain(request):
    from django.contrib.sites.shortcuts import get_current_site
    return get_current_site(request).domain


class LoginView(AuthThrottleMixin, generics.GenericAPIView):
    """Exchange email + password for an API token (send it as ``Authorization: Token <key>``)."""
    serializer_class = LoginSerializer
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email, password = serializer.validated_data['email'], serializer.validated_data['password']
        user = get_user_model().objects.filter(email__iexact=email).first()
        if user is None:
            get_user_model()().set_password(password)  # same hashing cost, so timing does not reveal accounts
            raise InvalidCredentialsException()
        if not user.check_password(password):
            raise InvalidCredentialsException()
        if not user.is_active:
            # Only reachable with the right password, so this does not reveal which emails exist.
            raise EmailNotVerifiedException() if not user.is_verified else AccountDisabledException()
        if user.two_factor_enabled:
            from .two_factor import check_code
            check_code(user, serializer.validated_data.get('otp_code'))  # otp_required / invalid_otp
        token, _ = Token.objects.get_or_create(user=user)
        return Response({'success': True, 'token': token.key, 'user': UserSerializer(user).data})


class LogoutView(generics.GenericAPIView):
    """Revoke the token used for this request."""
    permission_classes = [IsAuthenticated]
    serializer_class = None

    def post(self, request):
        from django.contrib.auth import logout
        if isinstance(request.auth, Token):
            request.auth.delete()
        else:  # session login: end the session and revoke API tokens too
            Token.objects.filter(user=request.user).delete()
            logout(request)
        return Response({'success': True})


class ResendVerificationView(AuthThrottleMixin, generics.GenericAPIView):
    """Send the verification email again. Always succeeds, so it cannot be used to probe for accounts."""
    serializer_class = EmailSerializer
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        from .tasks import send_verification_email
        user = get_user_model().objects.filter(email__iexact=serializer.validated_data['email']).first()
        if user is not None and not user.is_verified:
            send_verification_email.delay(user.pk, _request_domain(request))
        return Response({'success': True})


class ForgotPasswordView(AuthThrottleMixin, generics.GenericAPIView):
    """Email a password reset link. Always succeeds, so it cannot be used to probe for accounts."""
    serializer_class = EmailSerializer
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        from .tasks import send_password_reset_email
        user = get_user_model().objects.filter(email__iexact=serializer.validated_data['email']).first()
        if user is not None and user.is_active and user.has_usable_password():
            send_password_reset_email.delay(user.pk, _request_domain(request))
        return Response({'success': True})


class ResetPasswordView(AuthThrottleMixin, generics.GenericAPIView):
    """Set a new password with the uid and token from the reset email. Signs out every session."""
    serializer_class = PasswordResetSerializer
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        from django.utils.encoding import force_str
        from django.utils.http import urlsafe_base64_decode

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            user = get_user_model().objects.get(pk=force_str(urlsafe_base64_decode(data['uid'])))
        except (TypeError, ValueError, OverflowError, get_user_model().DoesNotExist):
            raise InvalidTokenException()
        if not user.is_active or not default_token_generator.check_token(user, data['token']):
            raise InvalidTokenException()
        _validate_new_password(data['new_password'], user)
        from .two_factor import lock_withdrawals
        with transaction.atomic():
            user.set_password(data['new_password'])  # also invalidates the reset token
            # Whoever controls the email could reset the password: hold outgoing funds for a while.
            lock_withdrawals(user, save=False)
            user.save(update_fields=['password', 'withdrawals_locked_until'])
            Token.objects.filter(user=user).delete()
            notify(user, 'Your password was reset',
                   ['Your password was reset and every session was signed out.',
                    'Withdrawals and transfers are paused until %s.' % _fmt(user.withdrawals_locked_until)])
        return Response({'success': True})


class MeView(generics.RetrieveUpdateAPIView):
    """My profile. ``first_name`` and ``last_name`` can be changed; email cannot."""
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'patch', 'head', 'options']

    def get_object(self):
        return self.request.user


class ChangePasswordView(generics.GenericAPIView):
    """Change my password. Other sessions are signed out; the response carries a new token."""
    serializer_class = PasswordChangeSerializer
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]  # the old-password check must not allow guessing
    throttle_scope = 'auth'

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        if not user.check_password(serializer.validated_data['old_password']):
            raise InvalidCredentialsException()
        _validate_new_password(serializer.validated_data['new_password'], user)
        with transaction.atomic():
            user.set_password(serializer.validated_data['new_password'])
            user.save(update_fields=['password'])
            Token.objects.filter(user=user).delete()
            token = Token.objects.create(user=user)
        return Response({'success': True, 'token': token.key})


# --------------------------------------------------------------------------- 2FA, whitelist
from django.utils import timezone
from rest_framework import mixins

from django_central_wallet.utils.custom_exception_handler import (
    AddressAlreadySavedException, TwoFactorAlreadyEnabledException, TwoFactorRequiredException,
)

from . import two_factor


def _fmt(dt):
    return timezone.localtime(dt).strftime('%Y-%m-%d %H:%M %Z') if dt else ''


def notify(user, subject, lines):
    """Security notice email, sent by the worker once the transaction commits."""
    from .tasks import send_security_notice
    lines = list(lines)
    transaction.on_commit(lambda: send_security_notice.delay(user.pk, subject, lines))


def _check_password(user, password):
    if not user.check_password(password):
        raise InvalidCredentialsException()


class SensitiveActionMixin:
    """Password and code checks must not allow guessing."""
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth'


class TwoFactorSetupView(SensitiveActionMixin, generics.GenericAPIView):
    """Start 2FA setup with the account password. Returns the secret, an ``otpauth://`` URI and a QR code
    to scan with an authenticator app; confirm with ``/account/2fa/enable/``."""
    serializer_class = PasswordConfirmSerializer
    permission_classes = [IsAuthenticated]

    def post(self, request):
        import io

        import pyqrcode

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        _check_password(user, serializer.validated_data['password'])
        if user.two_factor_enabled:
            raise TwoFactorAlreadyEnabledException()
        secret, uri = two_factor.start_setup(user)
        buffer = io.BytesIO()
        pyqrcode.create(uri).svg(buffer, scale=4, quiet_zone=2)
        return Response({'success': True, 'secret': secret, 'otpauth_uri': uri,
                         'qr_code_svg': buffer.getvalue().decode()})


class TwoFactorEnableView(SensitiveActionMixin, generics.GenericAPIView):
    """Finish 2FA setup with the first code from the authenticator app."""
    serializer_class = OTPCodeSerializer
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        if user.two_factor_enabled:
            raise TwoFactorAlreadyEnabledException()
        with transaction.atomic():
            two_factor.confirm_setup(user, serializer.validated_data['otp_code'])
            notify(user, 'Two-factor authentication enabled',
                   ['Two-factor authentication is now on for your account.'])
        return Response({'success': True, 'user': UserSerializer(user).data})


class TwoFactorDisableView(SensitiveActionMixin, generics.GenericAPIView):
    """Turn 2FA off (password and a current code). Withdrawals and transfers pause for the security cooldown."""
    serializer_class = TwoFactorDisableSerializer
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        _check_password(user, serializer.validated_data['password'])
        if not user.two_factor_enabled:
            raise TwoFactorRequiredException()
        with transaction.atomic():
            two_factor.disable(user, serializer.validated_data['otp_code'])
            notify(user, 'Two-factor authentication disabled',
                   ['Two-factor authentication was turned off for your account.',
                    'Withdrawals and transfers are paused until %s.' % _fmt(user.withdrawals_locked_until)])
        return Response({'success': True, 'user': UserSerializer(user).data})


class WithdrawalAddressViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, mixins.DestroyModelMixin,
                               viewsets.GenericViewSet):
    """My saved withdrawal addresses. Adding one needs 2FA and a code; it becomes usable after
    ``WITHDRAWAL_ADDRESS_COOLDOWN_HOURS``. Removing one needs nothing (it only restricts)."""
    serializer_class = WithdrawalAddressSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return WithdrawalAddress.objects.none()
        return WithdrawalAddress.objects.filter(user=self.request.user)

    def get_throttles(self):
        if self.action == 'create':
            self.throttle_scope = 'auth'
            return [ScopedRateThrottle()]
        return super().get_throttles()

    def perform_create(self, serializer):
        user = self.request.user
        if not user.two_factor_enabled:
            raise TwoFactorRequiredException()
        address = serializer.validated_data['address']
        otp_code = serializer.validated_data.pop('otp_code')
        with transaction.atomic():
            two_factor.check_code(user, otp_code)
            if WithdrawalAddress.objects.filter(user=user, address__iexact=address).exists():
                raise AddressAlreadySavedException()
            saved = serializer.save(user=user, active_from=timezone.now() + two_factor.address_cooldown())
            notify(user, 'New withdrawal address added',
                   [f'The address {saved.address} ({saved.label or "no label"}) was added to your saved addresses.',
                    f'It can be used for withdrawals from {_fmt(saved.active_from)}.'])


class WhitelistModeView(generics.GenericAPIView):
    """Turn whitelist mode on (immediate) or off (needs a code; takes effect after the address cooldown).
    Needs 2FA. Turning it on again cancels a pending switch-off."""
    serializer_class = WhitelistModeSerializer
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth'

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        if not user.two_factor_enabled:
            raise TwoFactorRequiredException()
        with transaction.atomic():
            if serializer.validated_data['enabled']:
                user.withdrawal_whitelist_enabled = True
                user.whitelist_disable_requested_at = None
            elif two_factor.whitelist_active(user):
                if user.whitelist_disable_requested_at is None:
                    two_factor.check_code(user, serializer.validated_data.get('otp_code'))
                    user.whitelist_disable_requested_at = timezone.now()
                    notify(user, 'Whitelist mode will be turned off',
                           ['A request to turn off withdrawal address whitelist mode was made.',
                            'It takes effect at %s. Turn whitelist mode on again to cancel.'
                            % _fmt(user.whitelist_disable_requested_at + two_factor.address_cooldown())])
            else:  # already off, or the switch-off has taken effect
                user.withdrawal_whitelist_enabled = False
                user.whitelist_disable_requested_at = None
            user.save(update_fields=['withdrawal_whitelist_enabled', 'whitelist_disable_requested_at'])
        return Response({'success': True, 'user': UserSerializer(user).data})
