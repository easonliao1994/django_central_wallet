"""Withdrawal workflow.

pending_review --approve--> approved --broadcast--> broadcast --receipt ok--> confirmed
      |                        |                       |  \\--receipt status 0--> failed (refunded)
      +--------reject----------+--> rejected (refunded) \\--not mined in time--> stuck --rebroadcast--> broadcast

* The balance is debited when the request is created, so it cannot be spent twice.
* Before sending, the signed transaction hash and nonce are committed to the database.
  If the process dies after the node accepted the transaction, the hash is not lost and
  the receipt poller still picks it up.
* A JSON-RPC error from the node (``ValueError`` in web3.py) means the node rejected the
  transaction, so it goes back to ``approved`` and can be retried. Any other error
  (timeout, connection reset) is ambiguous: the withdrawal stays ``broadcast`` and the
  receipt poller decides.
* Confirmation is checked with ``eth_getTransactionReceipt`` on the known hashes, not by
  scanning blocks, so failed (reverted) withdrawals are detected and refunded.
"""
import logging
import math
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import List, Optional

from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from django_central_wallet.utils.custom_exception_handler import (
    AmountBelowMinimumException, DailyLimitExceededException, InsufficientBalanceException,
    InvalidAddressException, InvalidAmountException, WithdrawalNotAllowedException,
)

from .models import CoinInfo, CoinInfoBalance, CryptoAddress, HotWallet, TransactionLog, Withdrawal

logger = logging.getLogger(__name__)

TRANSFER_SELECTOR = 'a9059cbb'  # transfer(address,uint256)
STUCK_AFTER = timedelta(minutes=30)
REBROADCAST_GAS_BUMP = Decimal('1.125')  # nodes require >= 10% bump to replace a pending tx


class BroadcastError(Exception):
    pass


# --------------------------------------------------------------------------- helpers
def parse_amount(coin_info: CoinInfo, amount) -> int:
    """Convert a human amount (e.g. '1.5') to the smallest on-chain unit."""
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        raise InvalidAmountException()
    if not value.is_finite() or value <= 0:
        raise InvalidAmountException()
    raw = value * (Decimal(10) ** coin_info.decimal)
    if raw != raw.to_integral_value():
        raise InvalidAmountException()  # more decimals than the token supports
    return int(raw)


def _checksum(address: str) -> str:
    from web3 import Web3
    if not isinstance(address, str) or not Web3.isAddress(address):
        raise InvalidAddressException()
    return Web3.toChecksumAddress(address)


def _encode_erc20_transfer(to: str, amount: int) -> str:
    try:
        from eth_abi import encode as abi_encode
    except ImportError:  # eth_abi < 4
        from eth_abi import encode_abi as abi_encode
    return '0x' + TRANSFER_SELECTOR + abi_encode(['address', 'uint256'], [to, amount]).hex()


def _refund(w: Withdrawal, status: str, reason: str = ''):
    CoinInfoBalance.objects.filter(crypto_address=w.crypto_address, coin_info=w.coin_info) \
        .update(external_amount=F('external_amount') + w.amount)
    w.status = status
    if reason:
        w.error_message = reason
    w.save()
    if w.transaction_log_id:
        TransactionLog.objects.filter(pk=w.transaction_log_id).update(status=TransactionLog.Status.FAILED)


def _get_w3(blockchain, w3):
    if w3 is not None:
        return w3
    w3 = blockchain.get_web3(middleware_inject=True)
    if w3 is None:
        raise BroadcastError(f'No reachable RPC endpoint for {blockchain}')
    return w3


# --------------------------------------------------------------------------- request
def request_withdrawal(user, coin_info: CoinInfo, to_address: str, amount: int) -> Withdrawal:
    """Validate, debit the balance and create the withdrawal (amount in smallest unit)."""
    if coin_info.blockchain_id is None or coin_info.coin_id is None or not coin_info.withdrawal_enabled:
        raise WithdrawalNotAllowedException()
    to_address = _checksum(to_address)
    if amount <= 0:
        raise InvalidAmountException()
    if amount < coin_info.min_withdrawal_amount:
        raise AmountBelowMinimumException()

    with transaction.atomic():
        crypto_address = CryptoAddress.objects.filter(user=user).order_by('id').first()
        if crypto_address is None:
            raise InsufficientBalanceException()
        if crypto_address.address.lower() == to_address.lower():
            raise InvalidAddressException()

        balance = CoinInfoBalance.objects.select_for_update() \
            .filter(crypto_address=crypto_address, coin_info=coin_info).first()
        if balance is None or balance.amount < amount:
            raise InsufficientBalanceException()

        if coin_info.daily_withdrawal_limit > 0:
            today_total = Withdrawal.objects.filter(
                user=user, coin_info=coin_info, created_at__date=timezone.localdate(),
            ).exclude(status__in=[Withdrawal.Status.REJECTED, Withdrawal.Status.FAILED]) \
                .aggregate(s=Sum('amount'))['s'] or 0
            if today_total + amount > coin_info.daily_withdrawal_limit:
                raise DailyLimitExceededException()

        CoinInfoBalance.objects.filter(pk=balance.pk).update(external_amount=F('external_amount') - amount)

        log = TransactionLog.objects.create(
            blockchain_id=coin_info.blockchain_id, coin_info=coin_info, user=user,
            direction=TransactionLog.Direction.WITHDRAWAL, status=TransactionLog.Status.PENDING,
            recipient_address=to_address, amount=amount,
        )
        auto = 0 < amount <= coin_info.auto_approve_withdrawal_limit
        return Withdrawal.objects.create(
            user=user, crypto_address=crypto_address, coin_info=coin_info,
            to_address=to_address, amount=amount, transaction_log=log,
            status=Withdrawal.Status.APPROVED if auto else Withdrawal.Status.PENDING_REVIEW,
        )


# --------------------------------------------------------------------------- review
def approve(w: Withdrawal, reviewer=None) -> bool:
    updated = Withdrawal.objects.filter(pk=w.pk, status=Withdrawal.Status.PENDING_REVIEW).update(
        status=Withdrawal.Status.APPROVED, reviewed_by=reviewer, reviewed_at=timezone.now())
    w.refresh_from_db()
    return bool(updated)


def reject(w: Withdrawal, reviewer=None, reason: str = 'Rejected by admin') -> bool:
    with transaction.atomic():
        w = Withdrawal.objects.select_for_update().get(pk=w.pk)
        if w.status not in (Withdrawal.Status.PENDING_REVIEW, Withdrawal.Status.APPROVED):
            return False
        w.reviewed_by = reviewer
        w.reviewed_at = timezone.now()
        _refund(w, Withdrawal.Status.REJECTED, reason)
    return True


# --------------------------------------------------------------------------- broadcast
def _build_tx(w: Withdrawal, hot: HotWallet, nonce: int, gas_price: int) -> dict:
    bc = w.coin_info.blockchain
    if not bc.chain_id:
        raise BroadcastError(f'{bc} has no chain_id; refusing to sign without replay protection')
    tx = {'nonce': nonce, 'gasPrice': gas_price, 'chainId': bc.chain_id}
    if w.coin_info.contract:
        tx.update({'to': _checksum(w.coin_info.contract), 'value': 0, 'gas': bc.gas_limit_erc20,
                   'data': _encode_erc20_transfer(_checksum(w.to_address), int(w.amount))})
    else:
        tx.update({'to': _checksum(w.to_address), 'value': int(w.amount), 'gas': bc.gas_limit})
    return tx


def _sign(tx: dict, hot: HotWallet):
    from eth_account import Account
    return Account.sign_transaction(tx, hot.get_private_key())


def _send(w3, w: Withdrawal, signed) -> bool:
    """Send a signed tx. Returns False only when the node definitely rejected it."""
    try:
        w3.eth.send_raw_transaction(signed.rawTransaction)
        return True
    except ValueError as e:  # JSON-RPC error: the node rejected the transaction
        if 'already known' in str(e).lower():
            return True
        w.error_message = str(e)
        return False
    except Exception as e:  # ambiguous (timeout, connection reset): it may have been accepted
        logger.warning('Withdrawal #%s: send failed ambiguously, waiting for receipt: %s', w.pk, e)
        Withdrawal.objects.filter(pk=w.pk).update(error_message=str(e))
        return True


def broadcast(w: Withdrawal, w3=None) -> bool:
    """Sign and send an approved withdrawal from the chain's hot wallet."""
    bc = w.coin_info.blockchain
    w3 = _get_w3(bc, w3)

    with transaction.atomic():
        w = Withdrawal.objects.select_for_update().select_related('coin_info__blockchain').get(pk=w.pk)
        if w.status != Withdrawal.Status.APPROVED:
            return False
        hot = HotWallet.objects.select_for_update().filter(blockchain=bc).first()
        if hot is None:
            raise BroadcastError(f'{bc} has no hot wallet')

        nonce = max(int(w3.eth.get_transaction_count(_checksum(hot.address), 'pending')), hot.next_nonce)
        gas_price = int(w3.eth.gas_price)
        signed = _sign(_build_tx(w, hot, nonce, gas_price), hot)
        tx_id = signed.hash.hex().lower()

        # Persist the hash before sending so it can never be lost.
        w.tx_id, w.nonce, w.gas_price = tx_id, nonce, gas_price
        w.status, w.broadcast_at, w.error_message = Withdrawal.Status.BROADCAST, timezone.now(), ''
        w.save()
        TransactionLog.objects.filter(pk=w.transaction_log_id).update(
            tx_id=tx_id, nonce=nonce, sender_address=hot.address)
        hot.next_nonce = nonce + 1
        hot.save(update_fields=['next_nonce'])

    if _send(w3, w, signed):
        logger.info('Withdrawal #%s broadcast as %s (nonce %s)', w.pk, tx_id, nonce)
        return True

    # Rejected by the node: the nonce was not used, so put the withdrawal back in the queue.
    with transaction.atomic():
        Withdrawal.objects.filter(pk=w.pk, tx_id=tx_id).update(
            status=Withdrawal.Status.APPROVED, tx_id=None, nonce=None, gas_price=None,
            broadcast_at=None, error_message=w.error_message)
        TransactionLog.objects.filter(pk=w.transaction_log_id, tx_id=tx_id).update(tx_id=None, nonce=None)
        HotWallet.objects.filter(pk=hot.pk, next_nonce=nonce + 1).update(next_nonce=nonce)
    logger.warning('Withdrawal #%s rejected by node: %s', w.pk, w.error_message)
    return False


def rebroadcast(w: Withdrawal, w3=None) -> bool:
    """Replace a stuck withdrawal with the same nonce and a higher gas price."""
    bc = w.coin_info.blockchain
    w3 = _get_w3(bc, w3)
    with transaction.atomic():
        w = Withdrawal.objects.select_for_update().get(pk=w.pk)
        if w.status not in (Withdrawal.Status.STUCK, Withdrawal.Status.BROADCAST) or w.nonce is None:
            return False
        hot = HotWallet.objects.get(blockchain=bc)
        gas_price = max(int(w3.eth.gas_price), math.ceil(Decimal(w.gas_price or 0) * REBROADCAST_GAS_BUMP) + 1)
        signed = _sign(_build_tx(w, hot, int(w.nonce), gas_price), hot)
        tx_id = signed.hash.hex().lower()
        w.previous_tx_ids = list(w.previous_tx_ids or []) + [w.tx_id]
        w.tx_id, w.gas_price = tx_id, gas_price
        w.status, w.broadcast_at, w.error_message = Withdrawal.Status.BROADCAST, timezone.now(), ''
        w.save()
        TransactionLog.objects.filter(pk=w.transaction_log_id).update(tx_id=tx_id)
    if not _send(w3, w, signed):
        # e.g. "nonce too low": an earlier broadcast was mined; the receipt poller will find it.
        Withdrawal.objects.filter(pk=w.pk).update(error_message=w.error_message)
        return False
    return True


def broadcast_approved(blockchain, w3=None, limit: int = 20) -> int:
    sent = 0
    qs = Withdrawal.objects.filter(coin_info__blockchain=blockchain, status=Withdrawal.Status.APPROVED).order_by('id')
    for w in qs[:limit]:
        try:
            sent += int(broadcast(w, w3))
        except BroadcastError as e:
            logger.error('Withdrawal #%s: %s', w.pk, e)
            break
    return sent


# --------------------------------------------------------------------------- confirmation
def _find_receipt(w3, hashes: List[str]):
    from web3.exceptions import TransactionNotFound
    for h in hashes:
        try:
            receipt = w3.eth.get_transaction_receipt(h)
        except TransactionNotFound:
            continue
        if receipt is not None:
            return h, receipt
    return None, None


def check_broadcast_withdrawals(blockchain, w3=None, stuck_after: Optional[timedelta] = None) -> dict:
    """Poll receipts of broadcast withdrawals and finalise them."""
    w3 = _get_w3(blockchain, w3)
    stuck_after = stuck_after or STUCK_AFTER
    latest = int(w3.eth.get_block_number())
    result = {'confirmed': 0, 'failed': 0, 'stuck': 0}

    qs = Withdrawal.objects.filter(coin_info__blockchain=blockchain,
                                   status__in=[Withdrawal.Status.BROADCAST, Withdrawal.Status.STUCK])
    for w in qs:
        mined_hash, receipt = _find_receipt(w3, [w.tx_id] + list(w.previous_tx_ids or []))
        if receipt is None:
            if w.status == Withdrawal.Status.BROADCAST and w.broadcast_at and w.broadcast_at < timezone.now() - stuck_after:
                Withdrawal.objects.filter(pk=w.pk, status=Withdrawal.Status.BROADCAST).update(status=Withdrawal.Status.STUCK)
                result['stuck'] += 1
            continue
        block_number = int(receipt['blockNumber'])
        if latest - block_number + 1 < blockchain.confirm_times:
            continue

        with transaction.atomic():
            w = Withdrawal.objects.select_for_update().get(pk=w.pk)
            if w.status not in (Withdrawal.Status.BROADCAST, Withdrawal.Status.STUCK):
                continue
            w.tx_id = mined_hash
            if int(receipt['status']) == 1:
                w.status = Withdrawal.Status.CONFIRMED
                w.save()
                TransactionLog.objects.filter(pk=w.transaction_log_id).update(
                    status=TransactionLog.Status.CONFIRMED, tx_id=mined_hash, block_number=block_number)
                result['confirmed'] += 1
            else:
                _refund(w, Withdrawal.Status.FAILED, 'Transaction reverted on chain')
                TransactionLog.objects.filter(pk=w.transaction_log_id).update(tx_id=mined_hash, block_number=block_number)
                result['failed'] += 1
    return result
