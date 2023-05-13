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
