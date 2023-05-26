from rest_framework import serializers
from django_central_wallet.utils.custom_exception_handler import *
from .models import *

class CoinSerializer(serializers.ModelSerializer):

    class Meta:
        model = Coin
        fields = ['symbol', 'name', 'server_decimal', 'scale']


class CoinInfoSerializer(serializers.ModelSerializer):
    coin = serializers.SerializerMethodField()

    def get_coin(self, obj):
        if obj.coin is not None:
            return CoinSerializer(obj.coin).data
        return None

    class Meta:
        model = CoinInfo
        fields = ['id', 'coin', 'contract', 'decimal']


class BlockchainSerializer(serializers.ModelSerializer):
    currency = serializers.SerializerMethodField()
    coins = serializers.SerializerMethodField()

    def get_currency(self, obj):
        if obj.coin is not None:
            return CoinSerializer(obj.coin).data
        return None
    
    def get_coins(self, obj):
        coins = CoinInfo.objects.filter(blockchain=obj, coin__isnull=False, contract__isnull=False)
        return CoinInfoSerializer(coins, many=True).data

    class Meta:
        model = Blockchain
        fields = ['id', 'name', 'block_explorer_url', 'gas_limit', 'gas_limit_erc20', 'currency', 'coins']
