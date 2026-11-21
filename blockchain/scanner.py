"""Chain scanner: detects deposits to platform addresses and credits balances.

Design notes
------------
* ERC20 deposits are found with ``eth_getLogs`` on the ``Transfer`` event of the
  supported contracts. Logs are only emitted by successful transactions, so a
  reverted transfer is never credited, and every transfer path (``transfer``,
  ``transferFrom``, multisend contracts, ...) is covered.
* Native coin transfers emit no logs, so blocks are read with full transactions.
  Only transactions that pay a platform address are checked further, and they
  are credited only when their receipt has ``status == 1``.
  Native coin sent from inside a contract (internal transactions) is not visible
  this way; balance reconciliation is the safety net for that.
* A deposit is identified by ``(blockchain, tx_id, log_index)`` and a database
  unique constraint rejects duplicates, so rescanning any range is safe.
* The cursor (``Blockchain.last_block``) only moves after a whole range has been
  read and credited. Any RPC error aborts the run without moving it.
"""
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from .models import Blockchain, CoinInfo, CoinInfoBalance, CryptoAddress, TransactionLog

logger = logging.getLogger(__name__)

# keccak256("Transfer(address,address,uint256)")
TRANSFER_TOPIC = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'


class ScanError(Exception):
    """Raised when the chain cannot be read; the cursor is left untouched."""


@dataclass(frozen=True)
class Deposit:
    tx_id: str
    log_index: int
    block_number: int
    sender_address: Optional[str]
    recipient_address: str
    amount: int
    coin_info_id: int
    crypto_address_id: int
    user_id: int


def _to_hex(value) -> str:
    """Normalise HexBytes / bytes / str to a lowercase 0x-prefixed hex string."""
    if isinstance(value, (bytes, bytearray)):
        return '0x' + bytes(value).hex()
    if hasattr(value, 'hex') and not isinstance(value, str):
        h = value.hex()
        return (h if h.startswith('0x') else '0x' + h).lower()
    value = str(value).lower()
    return value if value.startswith('0x') else '0x' + value


def _topic_to_address(topic) -> str:
    return '0x' + _to_hex(topic)[-40:]


def _to_int(value) -> int:
    if isinstance(value, int):
        return value
    h = _to_hex(value)
    return int(h, 16) if h != '0x' else 0


class ChainScanner:
    def __init__(self, blockchain: Blockchain, w3=None):
        self.blockchain = blockchain
        self._w3 = w3

    # ------------------------------------------------------------------ setup
    @property
    def w3(self):
        if self._w3 is None:
            self._w3 = self.blockchain.get_web3(middleware_inject=True)
            if self._w3 is None:
                raise ScanError(f'No reachable RPC endpoint for {self.blockchain}')
        return self._w3

    def _platform_addresses(self) -> Dict[str, dict]:
        rows = CryptoAddress.objects.values('id', 'address', 'user_id')
        return {r['address'].lower(): r for r in rows}

    def _token_coin_infos(self) -> Dict[str, CoinInfo]:
        qs = CoinInfo.objects.filter(blockchain=self.blockchain, coin__isnull=False,
                                     contract__isnull=False).exclude(contract='')
        return {ci.contract.lower(): ci for ci in qs}

    # ------------------------------------------------------------- block range
    def target_block(self) -> int:
        """Highest block that is considered final."""
        if self.blockchain.use_finalized_block:
            try:
                return int(self.w3.eth.get_block('finalized')['number'])
            except Exception:
                logger.warning('%s: finalized tag unavailable, falling back to confirm_times', self.blockchain)
        try:
            latest = int(self.w3.eth.get_block_number())
        except Exception as e:
            raise ScanError(f'Failed to get latest block number: {e}') from e
        return latest - self.blockchain.confirm_times

    # ----------------------------------------------------------------- public
    def run(self) -> int:
        """Scan the next batch of confirmed blocks. Returns the number of deposits credited."""
        bc = self.blockchain
        target = self.target_block()

        if bc.last_block <= 0:
            # A fresh chain starts from the current confirmed block instead of genesis.
            Blockchain.objects.filter(pk=bc.pk).update(last_block=target, last_sync_time=timezone.now())
            bc.last_block = target
            logger.info('%s: cursor initialised at block %s', bc, target)
            return 0

        start = bc.last_block + 1
        if target < start:
            return 0
        end = min(target, bc.last_block + max(1, bc.scan_batch_size))

        deposits = self.collect_deposits(start, end)
        with transaction.atomic():
            credited = self.credit(deposits)
            Blockchain.objects.filter(pk=bc.pk).update(last_block=end, last_sync_time=timezone.now())
        bc.last_block = end
        logger.info('%s: scanned blocks %s-%s, %s new deposit(s)', bc, start, end, credited)
        return credited

    def rescan(self, block_number: int) -> int:
        """Rescan a single block (e.g. to recover a missed deposit). The cursor is not moved."""
        deposits = self.collect_deposits(block_number, block_number)
        with transaction.atomic():
            credited = self.credit(deposits)
        logger.info('%s: rescanned block %s, %s new deposit(s)', self.blockchain, block_number, credited)
        return credited

    # --------------------------------------------------------------- reading
    def collect_deposits(self, start: int, end: int) -> List[Deposit]:
        addresses = self._platform_addresses()
        if not addresses:
            return []
        deposits = self._collect_token_deposits(start, end, addresses)
        if self.blockchain.scan_native:
            deposits += self._collect_native_deposits(start, end, addresses)
        return deposits

    def _get_logs(self, start: int, end: int, contracts: List[str]) -> list:
        """eth_getLogs, halving the range when the provider rejects it."""
        try:
            return list(self.w3.eth.get_logs({
                'fromBlock': start,
                'toBlock': end,
                'address': contracts,
                'topics': [TRANSFER_TOPIC],
            }))
        except Exception as e:
            if start >= end:
                raise ScanError(f'eth_getLogs failed for block {start}: {e}') from e
            mid = (start + end) // 2
            logger.info('%s: eth_getLogs %s-%s rejected (%s), splitting', self.blockchain, start, end, e)
            return self._get_logs(start, mid, contracts) + self._get_logs(mid + 1, end, contracts)

    def _collect_token_deposits(self, start, end, addresses) -> List[Deposit]:
        from web3 import Web3

        coin_infos = self._token_coin_infos()
        if not coin_infos:
            return []
        contracts = [Web3.toChecksumAddress(c) for c in coin_infos]
        deposits = []
        for log in self._get_logs(start, end, contracts):
            if log.get('removed'):
                continue
            topics = log.get('topics') or []
            # ERC20 Transfer has exactly 3 topics; ERC721 Transfer (4 topics) is ignored.
            if len(topics) != 3 or _to_hex(topics[0]) != TRANSFER_TOPIC:
                continue
            recipient = _topic_to_address(topics[2])
            owner = addresses.get(recipient)
            coin_info = coin_infos.get(str(log['address']).lower())
            if owner is None or coin_info is None:
                continue
            amount = _to_int(log.get('data') or 0)
            if amount <= 0:
                continue
            deposits.append(Deposit(
                tx_id=_to_hex(log['transactionHash']),
                log_index=int(log['logIndex']),
                block_number=int(log['blockNumber']),
                sender_address=_topic_to_address(topics[1]),
                recipient_address=recipient,
                amount=amount,
                coin_info_id=coin_info.pk,
                crypto_address_id=owner['id'],
                user_id=owner['user_id'],
            ))
        return deposits

    def _collect_native_deposits(self, start, end, addresses) -> List[Deposit]:
        coin_info = self.blockchain.get_native_coin_info()
        if coin_info is None:
            return []
        deposits = []
        for number in range(start, end + 1):
            try:
                block = self.w3.eth.get_block(number, full_transactions=True)
            except Exception as e:
                raise ScanError(f'Failed to get block {number}: {e}') from e
            for tx in block.get('transactions', []):
                to = tx.get('to')
                value = _to_int(tx.get('value') or 0)
                if not to or value <= 0:
                    continue
                owner = addresses.get(str(to).lower())
                if owner is None:
                    continue
                tx_id = _to_hex(tx['hash'])
                try:
                    receipt = self.w3.eth.get_transaction_receipt(tx_id)
                except Exception as e:
                    raise ScanError(f'Failed to get receipt {tx_id}: {e}') from e
                if int(receipt.get('status', 0)) != 1:
                    logger.info('%s: skipping reverted native transfer %s', self.blockchain, tx_id)
                    continue
                deposits.append(Deposit(
                    tx_id=tx_id,
                    log_index=TransactionLog.NATIVE_LOG_INDEX,
                    block_number=number,
                    sender_address=str(tx.get('from') or '').lower() or None,
                    recipient_address=str(to).lower(),
                    amount=value,
                    coin_info_id=coin_info.pk,
                    crypto_address_id=owner['id'],
                    user_id=owner['user_id'],
                ))
        return deposits

    # --------------------------------------------------------------- writing
    def credit(self, deposits: List[Deposit]) -> int:
        """Credit deposits; already-credited ones are skipped by the unique constraint."""
        credited = 0
        for d in deposits:
            try:
                with transaction.atomic():
                    TransactionLog.objects.create(
                        tx_id=d.tx_id,
                        log_index=d.log_index,
                        blockchain=self.blockchain,
                        coin_info_id=d.coin_info_id,
                        user_id=d.user_id,
                        direction=TransactionLog.Direction.DEPOSIT,
                        status=TransactionLog.Status.CONFIRMED,
                        sender_address=d.sender_address,
                        recipient_address=d.recipient_address,
                        amount=d.amount,
                        block_number=d.block_number,
                    )
                    balance, _ = CoinInfoBalance.objects.get_or_create(
                        crypto_address_id=d.crypto_address_id, coin_info_id=d.coin_info_id)
                    CoinInfoBalance.objects.filter(pk=balance.pk).update(
                        external_amount=F('external_amount') + d.amount)
            except IntegrityError:
                continue
            credited += 1
        return credited
