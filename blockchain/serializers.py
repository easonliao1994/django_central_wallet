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
    flow = serializers.SerializerMethodField(help_text="'in' or 'out' for the user the record belongs to; null for platform records.")
    internal_transfer = serializers.SerializerMethodField()

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

    def _transfer(self, obj):
        for name in ('transfer_as_sender', 'transfer_as_recipient'):
            try:
                return name, getattr(obj, name)
            except InternalTransfer.DoesNotExist:
                continue
        return None, None

    def get_flow(self, obj):
        D = TransactionLog.Direction
        if obj.direction == D.DEPOSIT:
            return 'in'
        if obj.direction == D.WITHDRAWAL:
            return 'out'
        if obj.direction == D.INTERNAL:
            side, t = self._transfer(obj)
            return None if t is None else ('out' if side == 'transfer_as_sender' else 'in')
        return None

    def get_internal_transfer(self, obj):
        _, t = self._transfer(obj)
        return t.pk if t else None

    class Meta:
        model = TransactionLog
        fields = ['id', 'user', 'direction', 'flow', 'status', 'blockchain_id', 'blockchain', 'coin_info', 'symbol',
                  'amount', 'amount_raw', 'from_address', 'to_address', 'tx_id', 'tx_url', 'block_number',
                  'withdrawal', 'withdrawal_status', 'internal_transfer', 'created_at']
        read_only_fields = fields


class InternalTransferSerializer(serializers.ModelSerializer):
    blockchain = serializers.CharField(source='coin_info.blockchain.name', read_only=True)
    symbol = serializers.CharField(source='coin_info.coin.symbol', read_only=True)
    amount = serializers.CharField(source='amount_str', read_only=True)
    flow = serializers.SerializerMethodField(help_text="'out' if I sent it, 'in' if I received it.")
    type = serializers.SerializerMethodField(help_text="Always 'internal_transfer' (lets clients tell it apart from a withdrawal).")
    to = serializers.SerializerMethodField(help_text='The recipient as I entered it (only for transfers I sent).')
    my_address = serializers.SerializerMethodField(help_text='My deposit address that was debited or credited.')

    def _is_sender(self, obj):
        request = self.context.get('request')
        return request is not None and obj.sender_id == request.user.pk

    def get_flow(self, obj):
        return 'out' if self._is_sender(obj) else 'in'

    def get_type(self, obj):
        return 'internal_transfer'

    def get_to(self, obj):
        return obj.recipient_input if self._is_sender(obj) else None

    def get_my_address(self, obj):
        return obj.sender_address if self._is_sender(obj) else obj.recipient_address

    class Meta:
        model = InternalTransfer
        fields = ['id', 'type', 'flow', 'blockchain', 'symbol', 'coin_info', 'amount', 'to', 'my_address',
                  'memo', 'created_at']
        read_only_fields = fields


class InternalTransferCreateSerializer(serializers.Serializer):
    coin_info = serializers.PrimaryKeyRelatedField(queryset=CoinInfo.objects.select_related('blockchain', 'coin'),
                                                   help_text='CoinInfo id (chain + coin) to transfer.')
    to = serializers.CharField(max_length=256, help_text="Recipient's email or platform deposit address.")
    amount = serializers.CharField(max_length=64, help_text="Human readable amount, e.g. '1.5'.")
    memo = serializers.CharField(max_length=140, required=False, allow_blank=True, default='')

    def create(self, validated_data):
        from .transfers import transfer
        from .withdrawals import parse_amount
        coin_info = validated_data['coin_info']
        return transfer(self.context['request'].user, coin_info, validated_data['to'],
                        parse_amount(coin_info, validated_data['amount']), validated_data.get('memo', ''))

    def to_representation(self, instance):
        return InternalTransferSerializer(instance, context=self.context).data
