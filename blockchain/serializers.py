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


class DepositAddressSerializer(serializers.ModelSerializer):
    qr_code_svg = serializers.SerializerMethodField()

    def get_qr_code_svg(self, obj) -> str:
        return obj.as_svg_qrcode(xml=True)

    class Meta:
        model = CryptoAddress
        fields = ['address', 'qr_code_svg']


class WithdrawalSerializer(serializers.ModelSerializer):
    blockchain = serializers.CharField(source='coin_info.blockchain.name', read_only=True)
    symbol = serializers.CharField(source='coin_info.coin.symbol', read_only=True)
    amount = serializers.CharField(source='amount_str', read_only=True)
    tx_url = serializers.SerializerMethodField()

    def get_tx_url(self, obj):
        explorer = obj.coin_info.blockchain.block_explorer_url if obj.coin_info.blockchain else None
        if not explorer or not obj.tx_id:
            return None
        return f"{explorer.rstrip('/')}/tx/{obj.tx_id}"

    class Meta:
        model = Withdrawal
        fields = ['id', 'blockchain', 'symbol', 'coin_info', 'to_address', 'amount', 'status',
                  'tx_id', 'tx_url', 'created_at', 'updated_at']
        read_only_fields = fields


class WithdrawalCreateSerializer(serializers.Serializer):
    coin_info = serializers.PrimaryKeyRelatedField(queryset=CoinInfo.objects.select_related('blockchain', 'coin'),
                                                   help_text='CoinInfo id (chain + coin) to withdraw.')
    to_address = serializers.CharField(max_length=256)
    amount = serializers.CharField(max_length=64, help_text="Human readable amount, e.g. '1.5'.")

    def create(self, validated_data):
        from .withdrawals import parse_amount, request_withdrawal
        coin_info = validated_data['coin_info']
        amount = parse_amount(coin_info, validated_data['amount'])
        return request_withdrawal(self.context['request'].user, coin_info, validated_data['to_address'], amount)

    def to_representation(self, instance):
        return WithdrawalSerializer(instance, context=self.context).data
