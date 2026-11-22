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


class BalanceSerializer(serializers.Serializer):
    """One coin on one chain. ``balance`` is rounded down to the coin's display scale."""
    coin_info = serializers.IntegerField(help_text='CoinInfo id (use it to withdraw or filter transactions).')
    blockchain_id = serializers.IntegerField()
    blockchain = serializers.CharField()
    symbol = serializers.CharField()
    name = serializers.CharField()
    contract = serializers.CharField(allow_null=True)
    decimal = serializers.IntegerField()
    balance = serializers.CharField(help_text="Human readable, rounded down to Coin.scale decimals, e.g. '1.5'.")
    balance_raw = serializers.CharField(help_text='Exact amount in the smallest on-chain unit.')


class TransactionSerializer(serializers.ModelSerializer):
    blockchain = serializers.CharField(source='blockchain.name', read_only=True, default=None)
    symbol = serializers.CharField(source='coin_info.coin.symbol', read_only=True, default=None)
    amount = serializers.CharField(source='amount_str', read_only=True)
    amount_raw = serializers.SerializerMethodField()
    from_address = serializers.CharField(source='sender_address', read_only=True)
    to_address = serializers.CharField(source='recipient_address', read_only=True)
    tx_url = serializers.SerializerMethodField()
    withdrawal = serializers.SerializerMethodField()
    withdrawal_status = serializers.SerializerMethodField()

    def get_amount_raw(self, obj) -> str:
        return str(int(obj.amount))

    def get_tx_url(self, obj):
        explorer = obj.blockchain.block_explorer_url if obj.blockchain else None
        if not explorer or not obj.tx_id:
            return None
        return f"{explorer.rstrip('/')}/tx/{obj.tx_id}"

    def _withdrawal(self, obj):
        try:
            return obj.withdrawal
        except Withdrawal.DoesNotExist:
            return None

    def get_withdrawal(self, obj):
        w = self._withdrawal(obj)
        return w.pk if w else None

    def get_withdrawal_status(self, obj):
        w = self._withdrawal(obj)
        return w.status if w else None

    class Meta:
        model = TransactionLog
        fields = ['id', 'user', 'direction', 'status', 'blockchain_id', 'blockchain', 'coin_info', 'symbol',
                  'amount', 'amount_raw', 'from_address', 'to_address', 'tx_id', 'tx_url', 'block_number',
                  'withdrawal', 'withdrawal_status', 'created_at']
        read_only_fields = fields
