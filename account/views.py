from django.shortcuts import render
from rest_framework import viewsets, permissions, status, generics
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from django_central_wallet.utils.global_method import *
from .models import *
from .serializers import *
from .tokens import EmailVerifyTokenGenerator
from django.contrib.auth import get_user_model
from django_auto_prefetching import AutoPrefetchViewSetMixin
from django_central_wallet.utils.paginations import CustomPagination
# Create your views here.

class UserViewSet(AutoPrefetchViewSetMixin, viewsets.ModelViewSet):
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


class AuthThrottleMixin:
    throttle_classes = [ScopedRateThrottle]
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
        if user is None or not user.check_password(password):
            raise InvalidCredentialsException()
        if not user.is_active:
            # Only reachable with the right password, so this does not reveal which emails exist.
            raise EmailNotVerifiedException() if not user.is_verified else AccountDisabledException()
        token, _ = Token.objects.get_or_create(user=user)
        return Response({'success': True, 'token': token.key, 'user': UserSerializer(user).data})


class LogoutView(generics.GenericAPIView):
    """Revoke the token used for this request."""
    permission_classes = [IsAuthenticated]
    serializer_class = None

    def post(self, request):
        if isinstance(request.auth, Token):
            request.auth.delete()
        else:
            Token.objects.filter(user=request.user).delete()
        return Response({'success': True})


class ResendVerificationView(AuthThrottleMixin, generics.GenericAPIView):
    """Send the verification email again. Always succeeds, so it cannot be used to probe for accounts."""
    serializer_class = EmailSerializer
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = get_user_model().objects.filter(email__iexact=serializer.validated_data['email']).first()
        if user is not None and not user.is_verified:
            user.send_verification_mail(_request_domain(request))
        return Response({'success': True})


class ForgotPasswordView(AuthThrottleMixin, generics.GenericAPIView):
    """Email a password reset link. Always succeeds, so it cannot be used to probe for accounts."""
    serializer_class = EmailSerializer
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = get_user_model().objects.filter(email__iexact=serializer.validated_data['email']).first()
        if user is not None and user.is_active and user.has_usable_password():
            user.send_password_reset_mail(_request_domain(request))
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
        with transaction.atomic():
            user.set_password(data['new_password'])  # also invalidates the reset token
            user.save(update_fields=['password'])
            Token.objects.filter(user=user).delete()
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
