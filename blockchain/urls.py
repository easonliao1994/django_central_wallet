from django.urls import path, include
from rest_framework.routers import SimpleRouter, DefaultRouter
from .views import BlockchainViewSet, BlockchainDetailView, DepositAddressView

router = DefaultRouter()
router.register('', BlockchainViewSet, basename='blockchains')

# Fixed paths must come before the router's '<pk>/' pattern.
urlpatterns = [
    path('deposit-address/', DepositAddressView.as_view(), name='deposit-address'),
] + router.urls + [
    path("<str:pk>/", BlockchainDetailView.as_view(), name="blockchain-detail"),
]