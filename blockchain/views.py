from django.shortcuts import render
from rest_framework import viewsets, permissions, status, generics
from django_auto_prefetching import AutoPrefetchViewSetMixin
from django.contrib.auth import get_user_model
from django_central_wallet.utils.paginations import CustomPagination
from .serializers import *
from django.shortcuts import get_object_or_404

# Create your views here.
class BlockchainViewSet(AutoPrefetchViewSetMixin, viewsets.ModelViewSet):
    queryset = Blockchain.objects.all()
    serializer_class = BlockchainSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = CustomPagination
    
    def get_queryset(self):
        return super().get_queryset()
    

class BlockchainDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = BlockchainSerializer
    permission_classes = [permissions.IsAuthenticated]

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
