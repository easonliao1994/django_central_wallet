import io
import logging

import pyqrcode as pyqrcode
from django.conf import settings
from django.db import models
from django.db.models import Q
from web3 import HTTPProvider, Web3

from django_central_wallet.utils.global_method import *

logger = logging.getLogger(__name__)

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
    gas_limit = models.PositiveIntegerField(default=21000, help_text='Minimum gas limit for native coin withdrawals. The actual limit is estimated per transaction (eth_estimateGas + margin).')
    gas_limit_erc20 = models.PositiveIntegerField(default=100000, help_text='Minimum gas limit for ERC20 withdrawals. The actual limit is estimated per transaction (eth_estimateGas + margin).')

    is_auto_sync = models.BooleanField(default=False, help_text='Set to true to automatically sync the blockchain network with our server.')
    scan_batch_size = models.PositiveIntegerField(default=200, help_text='Maximum number of blocks the chain scanner processes in a single run.')
    scan_native = models.BooleanField(default=True, help_text='Scan full blocks for native coin deposits. ERC20 deposits are always detected via Transfer logs.')
    use_finalized_block = models.BooleanField(default=False, help_text="Use the node's 'finalized' block tag instead of confirm_times (e.g. Ethereum PoS).")
    cold_wallet_address = models.CharField(max_length=64, blank=True, default='', help_text='Cold wallet address (its private key is never stored here). Sweeps go here instead of the hot wallet once the hot wallet holds more than a coin\'s hot_wallet_limit.')
    
    class Meta(object):
        unique_together = [("name")]
    
    def __str__(self):
        return f'{self.name}'
    
    RPC_TIMEOUT = 15  # seconds per JSON-RPC request

    def get_web3(self, middleware_inject=False):
        """Web3 connected to the first reachable RPC URL (one per line), or None.

        ``middleware_inject`` adds the proof-of-authority extraData middleware needed by
        chains such as BSC or Polygon; it is harmless on Ethereum.
        """
        for rpc_url in (self.rpc_url or '').splitlines():
            rpc_url = rpc_url.strip()
            if not rpc_url:
                continue
            try:
                w3 = Web3(HTTPProvider(rpc_url, request_kwargs={'timeout': self.RPC_TIMEOUT}))
                if w3.is_connected():
                    if middleware_inject:
                        from web3.middleware import ExtraDataToPOAMiddleware
                        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
                    return w3
            except Exception as e:
                logger.warning('%s: RPC endpoint unavailable: %s', self, e)
        return None

    def get_confirm_latest_block_number(self, w3):
        if w3 is None:
            w3 = self.get_web3()
        try:
            current_latest_block_number = w3.eth.get_block_number()
            confirm_block_number = current_latest_block_number - self.confirm_times
            return confirm_block_number
        except Exception:
            logger.exception('Failed to get latest block number for %s', self)
            return None

    def get_native_coin_info(self):
        """CoinInfo of the native coin (the one without a contract address)."""
        return CoinInfo.objects.filter(blockchain=self, coin__isnull=False)             .filter(Q(contract__isnull=True) | Q(contract='')).first()

    def get_support_contracts(self):
        from django.db.models.functions import Lower
        return list(CoinInfo.objects.annotate(contract_lower=Lower('contract')).filter(blockchain=self, contract__isnull=False).values_list('contract_lower', flat=True))
    
class CoinInfo(models.Model):
    blockchain = models.ForeignKey(Blockchain, on_delete=models.SET_NULL, blank=True, null=True, default=None, help_text='The blockchain this coin belongs to.')
    coin = models.ForeignKey(Coin, on_delete=models.SET_NULL, blank=True, null=True, default=None, help_text='The cryptocurrency coin that this information is associated with.')
    decimal = models.IntegerField(default=18, help_text='The number of decimal places for this coin.')
    contract = models.CharField(max_length=256, null=True, blank=True, help_text='The smart contract address associated with this coin (if applicable).')
    withdrawal_enabled = models.BooleanField(default=True, help_text='Allow users to withdraw this coin.')
    min_withdrawal_amount = models.DecimalField(default=0, max_digits=30, decimal_places=0, help_text='Minimum withdrawal amount, in the smallest on-chain unit.')
    daily_withdrawal_limit = models.DecimalField(default=0, max_digits=30, decimal_places=0, help_text='Maximum total withdrawn per user per day, in the smallest on-chain unit. 0 = unlimited.')
    auto_approve_withdrawal_limit = models.DecimalField(default=0, max_digits=30, decimal_places=0, help_text='Withdrawals up to this amount (smallest on-chain unit) skip admin review. 0 = every withdrawal needs review.')
    sweep_threshold = models.DecimalField(default=0, max_digits=30, decimal_places=0, help_text='Sweep a deposit address to the hot wallet once it holds at least this much (smallest on-chain unit). 0 = never sweep this coin.')
    hot_wallet_limit = models.DecimalField(default=0, max_digits=30, decimal_places=0, help_text='When the hot wallet already holds at least this much (smallest on-chain unit), sweep to the chain\'s cold wallet instead. 0 = always sweep to the hot wallet.')
    hot_wallet_alert_threshold = models.DecimalField(default=0, max_digits=30, decimal_places=0, help_text='Alert admins when the hot wallet holds less than this (smallest on-chain unit). 0 = off. An alert is also sent when it holds less than the withdrawals pending review or approved.')

    class Meta(object):
        unique_together = [('blockchain', 'coin')]
    
    def __str__(self):
        return f'{self.blockchain} - {self.coin.symbol}'
    
    @property
    def denominator(self)->int:
        return 10**self.decimal

class EncryptedPrivateKeyModel(models.Model):
    encrypted_private_key = models.TextField(editable=False, help_text='Private key encrypted with WALLET_ENCRYPTION_KEYS. Never displayed.')

    class Meta:
        abstract = True

    def set_private_key(self, private_key: str):
        from .key_vault import encrypt_secret
        self.encrypted_private_key = encrypt_secret(private_key)

    def get_private_key(self) -> str:
        """Decrypt the private key. Only call this right before signing a transaction."""
        from .key_vault import decrypt_secret
        return decrypt_secret(self.encrypted_private_key)


class CryptoAddress(EncryptedPrivateKeyModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='crypto_addresses')
    address = models.CharField(max_length=256, unique=True, help_text='Cryptocurrency wallet address.')

    def __str__(self):
        return self.address

    def as_svg_qrcode(self, xml=False):
        buffer = io.BytesIO()
        qr = pyqrcode.create(self.address)
        qr.svg(buffer, scale=4, quiet_zone=0)
        svg_xml = buffer.getvalue().decode('ascii')
        if not xml:
            svg_xml = svg_xml.split('>', 1)[-1]
        return svg_xml

class CoinInfoBalance(models.Model):
    crypto_address = models.ForeignKey(CryptoAddress, on_delete=models.PROTECT, help_text='Cryptocurrency wallet address.')
    coin_info = models.ForeignKey(CoinInfo, on_delete=models.PROTECT, null=False, help_text='Cryptocurrency information.')
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
    class Direction(models.TextChoices):
        DEPOSIT = 'deposit', 'Deposit'
        WITHDRAWAL = 'withdrawal', 'Withdrawal'
        INTERNAL = 'internal', 'Internal transfer'
        SWEEP = 'sweep', 'Sweep'
        GAS_TOPUP = 'gas_topup', 'Gas top-up'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        CONFIRMED = 'confirmed', 'Confirmed'
        FAILED = 'failed', 'Failed'

    # Native coin transfers have no log index; they use this sentinel instead.
    NATIVE_LOG_INDEX = -1

    tx_id = models.CharField(max_length=256, db_index=True, null=True, blank=True, help_text='Transaction hash (empty until a withdrawal is broadcast).')
    log_index = models.IntegerField(default=NATIVE_LOG_INDEX, help_text='Log index of the ERC20 Transfer event, -1 for native coin transfers.')
    blockchain = models.ForeignKey(Blockchain, on_delete=models.PROTECT, null=True, blank=True)
    coin_info = models.ForeignKey(CoinInfo, on_delete=models.PROTECT, null=True, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='transaction_logs')
    direction = models.CharField(max_length=16, choices=Direction.choices, default=Direction.DEPOSIT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    sender_address = models.CharField(max_length=256, null=True, blank=True)
    recipient_address = models.CharField(max_length=256, null=True, blank=True)
    amount = models.DecimalField(default=0, max_digits=30, decimal_places=0, help_text='Amount in the smallest on-chain unit.')

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    block_number = models.BigIntegerField(null=True, blank=True)
    nonce = models.BigIntegerField(null=True, blank=True)
    fee = models.DecimalField(default=0, max_digits=30, decimal_places=0, help_text='Native coin paid for gas by sender_address once mined (smallest unit). Recorded for transactions sent from platform addresses.')

    class Meta:
        constraints = [
            # The database, not a pre-check, guarantees a deposit is credited only once.
            models.UniqueConstraint(
                fields=['blockchain', 'tx_id', 'log_index'],
                condition=Q(direction='deposit'),
                name='unique_deposit_per_chain_tx_log',
            ),
        ]
        indexes = [
            models.Index(fields=['user', '-created_at']),
        ]

    def __str__(self):
        if self.blockchain is not None and self.coin_info is not None and self.coin_info.coin is not None:
            return f'[{self.blockchain.name} - {self.coin_info.coin.symbol}] - {self.tx_id}'
        return f'{self.tx_id}'

    @property
    def amount_str(self):
        return get_blockchain_real_amount(self.coin_info, self.amount)


class HotWallet(EncryptedPrivateKeyModel):
    """Platform wallet that signs and pays withdrawals on one blockchain."""
    blockchain = models.OneToOneField(Blockchain, on_delete=models.PROTECT, related_name='hot_wallet')
    address = models.CharField(max_length=256, editable=False)
    next_nonce = models.BigIntegerField(default=0, help_text='Next nonce to use. The higher of this and the pending nonce reported by the node is used.')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.blockchain} hot wallet {self.address}'


class Withdrawal(models.Model):
    class Status(models.TextChoices):
        PENDING_REVIEW = 'pending_review', 'Pending review'
        APPROVED = 'approved', 'Approved (waiting to broadcast)'
        BROADCAST = 'broadcast', 'Broadcast (waiting for confirmation)'
        STUCK = 'stuck', 'Stuck (not mined in time)'
        CONFIRMED = 'confirmed', 'Confirmed'
        FAILED = 'failed', 'Failed (refunded)'
        REJECTED = 'rejected', 'Rejected (refunded)'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='withdrawals')
    crypto_address = models.ForeignKey(CryptoAddress, on_delete=models.PROTECT, help_text='Address whose platform balance is debited.')
    coin_info = models.ForeignKey(CoinInfo, on_delete=models.PROTECT)
    to_address = models.CharField(max_length=256)
    amount = models.DecimalField(max_digits=30, decimal_places=0, help_text='Amount in the smallest on-chain unit.')
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING_REVIEW, db_index=True)

    tx_id = models.CharField(max_length=256, null=True, blank=True, db_index=True)
    previous_tx_ids = models.JSONField(default=list, blank=True, help_text='Hashes of earlier broadcasts replaced by a rebroadcast (same nonce).')
    nonce = models.BigIntegerField(null=True, blank=True)
    gas_price = models.DecimalField(max_digits=30, decimal_places=0, null=True, blank=True)
    gas_limit = models.PositiveIntegerField(null=True, blank=True, help_text='Gas limit the transaction was signed with.')
    broadcast_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True, default='')

    transaction_log = models.OneToOneField(TransactionLog, on_delete=models.SET_NULL, null=True, blank=True, related_name='withdrawal')
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-id']

    def __str__(self):
        return f'Withdrawal #{self.pk} {self.amount_str} {self.coin_info} -> {self.to_address} ({self.status})'

    @property
    def amount_str(self):
        return get_blockchain_real_amount(self.coin_info, self.amount)


class Sweep(models.Model):
    """Moves funds from a user deposit address to the hot (or cold) wallet.

    Token sweeps may first need native coin for gas: the hot wallet sends a gas
    top-up, and the sweep is signed once that is mined. Both legs keep their
    nonce once assigned and are rebroadcast with the same nonce when stuck, like
    withdrawals. Sweeps never change users' platform balances.
    """

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending (next: top up gas or send)'
        TOPPING_UP = 'topping_up', 'Waiting for gas top-up'
        BROADCAST = 'broadcast', 'Broadcast (waiting for confirmation)'
        CONFIRMED = 'confirmed', 'Confirmed'
        FAILED = 'failed', 'Failed'
        CANCELLED = 'cancelled', 'Cancelled (nothing sent)'

    OPEN_STATUSES = (Status.PENDING, Status.TOPPING_UP, Status.BROADCAST)

    blockchain = models.ForeignKey(Blockchain, on_delete=models.PROTECT, related_name='sweeps')
    coin_info = models.ForeignKey(CoinInfo, on_delete=models.PROTECT)
    crypto_address = models.ForeignKey(CryptoAddress, on_delete=models.PROTECT, related_name='sweeps')
    to_address = models.CharField(max_length=64, help_text='Hot or cold wallet address.')
    amount = models.DecimalField(max_digits=30, decimal_places=0, null=True, blank=True, help_text='Amount sent (smallest unit); for the native coin the balance minus the gas fee.')
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    error_message = models.TextField(blank=True, default='')

    # Sweep transaction, signed by the deposit address.
    tx_id = models.CharField(max_length=256, null=True, blank=True, db_index=True)
    previous_tx_ids = models.JSONField(default=list, blank=True)
    nonce = models.BigIntegerField(null=True, blank=True)
    gas_price = models.DecimalField(max_digits=30, decimal_places=0, null=True, blank=True)
    gas_limit = models.PositiveIntegerField(null=True, blank=True)
    broadcast_at = models.DateTimeField(null=True, blank=True)
    transaction_log = models.OneToOneField(TransactionLog, on_delete=models.SET_NULL, null=True, blank=True, related_name='sweep')

    # Gas top-up transaction, signed by the hot wallet (token sweeps only).
    topup_tx_id = models.CharField(max_length=256, null=True, blank=True, db_index=True)
    topup_previous_tx_ids = models.JSONField(default=list, blank=True)
    topup_nonce = models.BigIntegerField(null=True, blank=True)
    topup_gas_price = models.DecimalField(max_digits=30, decimal_places=0, null=True, blank=True)
    topup_gas_limit = models.PositiveIntegerField(null=True, blank=True)
    topup_amount = models.DecimalField(max_digits=30, decimal_places=0, null=True, blank=True)
    topup_broadcast_at = models.DateTimeField(null=True, blank=True)
    topup_count = models.PositiveSmallIntegerField(default=0)
    topup_log = models.ForeignKey(TransactionLog, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    send_attempts = models.PositiveSmallIntegerField(default=0, help_text='Failed attempts to sign/send (e.g. gas estimation errors); cancelled after too many.')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-id']
        constraints = [
            # One sweep at a time per address and chain: they share the address nonce and its native balance.
            models.UniqueConstraint(
                fields=['blockchain', 'crypto_address'],
                condition=Q(status__in=['pending', 'topping_up', 'broadcast']),
                name='one_open_sweep_per_chain_address',
            ),
        ]

    def __str__(self):
        return f'Sweep #{self.pk} {self.coin_info} {self.crypto_address.address} -> {self.to_address} ({self.status})'


class InternalTransfer(models.Model):
    """Off-chain transfer between two users (see blockchain/transfers.py)."""
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='sent_transfers')
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='received_transfers')
    coin_info = models.ForeignKey(CoinInfo, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=30, decimal_places=0, help_text='Amount in the smallest on-chain unit.')
    sender_address = models.CharField(max_length=64, help_text="Sender's deposit address whose balance was debited.")
    recipient_address = models.CharField(max_length=64, help_text="Recipient's deposit address whose balance was credited.")
    recipient_input = models.CharField(max_length=256, blank=True, default='', help_text='What the sender entered as recipient (email or address); shown to the sender.')
    memo = models.CharField(max_length=140, blank=True, default='')
    sender_log = models.OneToOneField(TransactionLog, on_delete=models.SET_NULL, null=True, blank=True, related_name='transfer_as_sender')
    recipient_log = models.OneToOneField(TransactionLog, on_delete=models.SET_NULL, null=True, blank=True, related_name='transfer_as_recipient')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']

    def __str__(self):
        return f'Transfer #{self.pk} {self.amount_str} {self.coin_info} {self.sender} -> {self.recipient}'

    @property
    def amount_str(self):
        return get_blockchain_real_amount(self.coin_info, self.amount)
