from django.urls import path, include
from rest_framework.routers import SimpleRouter, DefaultRouter
from .views import BlockchainViewSet, BlockchainDetailView

router = DefaultRouter()
router.register('', BlockchainViewSet, basename='blockchains')

urlpatterns = router.urls + [
    path("<str:pk>/", BlockchainDetailView.as_view(), name="blockchain-detail"),
]