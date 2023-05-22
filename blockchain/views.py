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