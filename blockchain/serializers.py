from rest_framework import serializers
from django_central_wallet.utils.custom_exception_handler import *
from .models import *

class CoinSerializer(serializers.ModelSerializer):

    class Meta:
        model = Coin
        fields = ['symbol', 'name', 'server_decimal', 'scale']


class BlockchainSerializer(serializers.ModelSerializer):
    coin = serializers.SerializerMethodField()

    def get_coin(self, obj):
        if obj.coin is not None:
            return CoinSerializer(obj.coin).data
        return None

    class Meta:
        model = Blockchain
        fields = ['id', 'name', 'block_explorer_url', 'gas_limit', 'gas_limit_erc20', 'coin']
