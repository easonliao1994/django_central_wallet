from django.urls import path, include
from rest_framework.routers import SimpleRouter, DefaultRouter
from .views import (BalanceListView, BlockchainDetailView, BlockchainViewSet, DepositAddressView,
                    InternalTransferViewSet, TransactionViewSet, WithdrawalViewSet)

router = DefaultRouter()
# Register fixed prefixes before '' so the blockchain '<pk>/' pattern does not swallow them.
router.register('withdrawals', WithdrawalViewSet, basename='withdrawals')
router.register('transactions', TransactionViewSet, basename='transactions')
router.register('transfers', InternalTransferViewSet, basename='transfers')
router.register('', BlockchainViewSet, basename='blockchains')

# Fixed paths must come before the router's '<pk>/' pattern.
urlpatterns = [
    path('deposit-address/', DepositAddressView.as_view(), name='deposit-address'),
    path('balances/', BalanceListView.as_view(), name='balances'),
] + router.urls + [
    path("<str:pk>/", BlockchainDetailView.as_view(), name="blockchain-detail"),
]