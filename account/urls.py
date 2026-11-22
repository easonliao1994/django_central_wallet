from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views
from .views import UserViewSet

router = SimpleRouter()
router.register('withdrawal-addresses', views.WithdrawalAddressViewSet, basename='withdrawal-address')

urlpatterns = [
    path('activate/<uidb64>/<token>/', views.activate, name='activate'),
    path('register/', UserViewSet.as_view({'post': 'create'}), name='register'),
    path('resend-verification/', views.ResendVerificationView.as_view(), name='resend-verification'),
    path('login/', views.LoginView.as_view(), name='login'),
    path('logout/', views.LogoutView.as_view(), name='logout'),
    path('password/forgot/', views.ForgotPasswordView.as_view(), name='password-forgot'),
    path('password/reset/', views.ResetPasswordView.as_view(), name='password-reset'),
    path('me/', views.MeView.as_view(), name='me'),
    path('me/password/', views.ChangePasswordView.as_view(), name='me-password'),
    path('2fa/setup/', views.TwoFactorSetupView.as_view(), name='2fa-setup'),
    path('2fa/enable/', views.TwoFactorEnableView.as_view(), name='2fa-enable'),
    path('2fa/disable/', views.TwoFactorDisableView.as_view(), name='2fa-disable'),
    path('withdrawal-whitelist/', views.WhitelistModeView.as_view(), name='withdrawal-whitelist'),
] + router.urls
