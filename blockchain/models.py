from django.db import models
from django_central_wallet.utils.global_method import *
from django.contrib.auth.models import User
import io
import pyqrcode as pyqrcode

class Coin(models.Model):
    symbol = models.CharField(max_length=32, default='ETH', help_text="Same with blockchain symbol.")
    name = models.CharField(max_length=32, default='Ethereum', help_text="Same with blockchain token name.")
    server_decimal = models.IntegerField(default=9, help_text='Number of decimal places used by the blockchain to store the token value.')
    scale = models.IntegerField(default=8, help_text='Maximum number of decimal places to be displayed.')

    class Meta(object):
        unique_together = [('symbol')]

    def __str__(self):
        return f'{self.symbol}'
    
    @property
    def denominator(self)->int:
        return 10**self.server_decimal

class Blockchain(models.Model):
    name = models.CharField(max_length=64, help_text='The name of the blockchain network.')
    rpc_url = models.TextField(null=True, blank=True, help_text='The URL of the JSON-RPC endpoint for the blockchain network.')
    chain_id = models.PositiveIntegerField(null=True, blank=True, help_text='The chain ID of the blockchain network.')
    coin = models.ForeignKey(Coin, on_delete=models.SET_NULL, blank=True, null=True, default=None, help_text='The cryptocurrency associated with this blockchain network.')
    block_explorer_url = models.URLField(null=True, blank=True, help_text='The URL of the block explorer for the blockchain network.')
    last_block = models.BigIntegerField(default=0, help_text="The last block synced by our server for this blockchain network.")
    last_sync_time = models.DateTimeField(auto_now=True, help_text='The timestamp of the last time our server synced with the blockchain network.')
    confirm_times = models.BigIntegerField(default=30, help_text="The number of confirmations required for a block to be considered final on the blockchain network.")
    gas_limit = models.PositiveIntegerField(default=21000, help_text='The gas limit for transactions on the blockchain network.')
    gas_limit_erc20 = models.PositiveIntegerField(default=100000, help_text='The gas limit for ERC20 token transactions on the blockchain network.')

    is_auto_sync = models.BooleanField(default=False, help_text='Set to true to automatically sync the blockchain network with our server.')
    
    class Meta(object):
        unique_together = [("name")]
    
    def __str__(self):
        return f'{self.name}'

    def get_confirm_latest_block_number(self, w3):
        if w3 is None:
            return None
        try:
            current_latest_block_number = w3.eth.get_block_number()
            confirm_block_number = current_latest_block_number - self.confirm_times
            return confirm_block_number
        except Exception as e:
            print(e)
            return None
    
    def get_support_contracts(self):
        from django.db.models.functions import Lower
        return list(CoinInfo.objects.annotate(contract_lower=Lower('contract')).filter(blockchain=self, contract__isnull=False).values_list('contract_lower', flat=True))
    
class CoinInfo(models.Model):
    blockchain = models.ForeignKey(Blockchain, on_delete=models.SET_NULL, blank=True, null=True, default=None, help_text='The blockchain this coin belongs to.')
    coin = models.ForeignKey(Coin, on_delete=models.SET_NULL, blank=True, null=True, default=None, help_text='The cryptocurrency coin that this information is associated with.')
    decimal = models.IntegerField(default=18, help_text='The number of decimal places for this coin.')
    contract = models.CharField(max_length=256, null=True, blank=True, help_text='The smart contract address associated with this coin (if applicable).')

    class Meta(object):
        unique_together = [('blockchain', 'coin')]
    
    def __str__(self):
        return f'{self.blockchain} - {self.coin.symbol}'
    
    @property
    def denominator(self)->int:
        return 10**self.decimal

class CryptoAddress(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='crypto_addresses')
    address = models.CharField(max_length=256, unique=True, help_text='Cryptocurrency wallet address.')
    private_key = models.CharField(max_length=256, help_text='Cryptocurrency wallet private key.')

    def as_svg_qrcode(self, xml=False):
        buffer = io.BytesIO()
        qr = pyqrcode.create(self.address)
        qr.svg(buffer, scale=4, quiet_zone=0)
        svg_xml = buffer.getvalue().decode('ascii')
        if not xml:
            svg_xml = svg_xml.split('>', 1)[-1]
        return svg_xml

class CoinInfoBalance(models.Model):
    crypto_address = models.ForeignKey(CryptoAddress, on_delete=models.CASCADE, help_text='Cryptocurrency wallet address.')
    coin_info = models.ForeignKey(CoinInfo, on_delete=models.CASCADE, null=False, help_text='Cryptocurrency information.')
    internal_amount = models.DecimalField(default=0, max_digits=30, decimal_places=0, help_text='Amount of coins in internal transfer.')
    external_amount = models.DecimalField(default=0, max_digits=30, decimal_places=0, help_text='Amount of coins in external transfer.')

    class Meta(object):
        unique_together = [('crypto_address', 'coin_info')]
    
    @property
    def amount(self) -> int:
        return self.internal_amount + self.external_amount

    @property
    def amount_str(self) -> str:
        """Return all amount in terms of the unit on the blockchain.
        """
        if self.coin_info is None:
            return None
        real_amount = get_blockchain_real_amount(self.coin_info, self.amount)
        if real_amount is not None:
            return f'{real_amount} {self.coin_info.coin.symbol}'
        else:
            return None
    
    @property
    def amount_in_server(self) -> str:
        return get_blockchain_real_amount(self.coin_info, self.amount)

class TransactionLog(models.Model):
    tx_id = models.CharField(max_length=256, db_index=True)
    blockchain = models.ForeignKey(Blockchain, on_delete=models.CASCADE, null=True, blank=True)
    coin_info = models.ForeignKey(CoinInfo, on_delete=models.CASCADE, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sender_address = models.CharField(max_length=256, null=True, blank=True)
    recipient_address = models.CharField(max_length=256, null=True, blank=True)
    amount = models.DecimalField(default=0, max_digits=30, decimal_places=0)
    
    status = models.CharField(max_length=100)
    block_number = models.PositiveIntegerField()
    nonce = models.PositiveIntegerField()

    def __str__(self):
        if self.blockchain is not None and self.coin_info is not None:
            return f'[{self.blockchain.name} - {self.coin_info.coin.symbol}] - {self.tx_id}'
        return f'{self.tx_id}'
    