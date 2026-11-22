"""Withdrawal workflow.

pending_review --approve--> approved --broadcast--> broadcast --final receipt ok--> confirmed
      |                        |                    |    |  \\--final receipt failed--> failed (refunded)
      +--------reject----------+--> rejected        |    \\--not mined in time--> stuck --auto rebroadcast--> broadcast
                                    (refunded)      \\--nonce consumed by another tx, none of ours mined--> approved

Safety rules
------------
* The balance is debited when the request is created, so it cannot be spent twice.
* A withdrawal gets a nonce exactly once. Every later (re)broadcast reuses that nonce, so
  at most one of its transactions can ever be mined: no double payout, whatever errors
  the RPC node returns, and no nonce gaps that would block later withdrawals.
* The signed transaction hash and nonce are committed before sending, so a crash after
  the node accepted the transaction cannot lose it.
* Send errors never refund or requeue directly: RPC gateways can return an error after
  forwarding a transaction. The withdrawal stays ``broadcast``; if nothing is mined it
  becomes ``stuck`` and is rebroadcast with the same nonce and a higher gas price.
* It goes back to ``approved`` (and gets a new nonce) only when the nonce was consumed in
  a final block by some other transaction and none of its own hashes has a receipt,
  i.e. it provably can no longer be mined.
* Confirmation uses ``eth_getTransactionReceipt`` on the known hashes plus the chain's
  confirmation rule. Token withdrawals also require the expected ``Transfer`` log, since
  some tokens return ``false`` instead of reverting.
* Once a withdrawal has a nonce, it cannot be rejected (it may already be paid).
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
from .scanner import TRANSFER_TOPIC, ScanError, _to_hex, _to_int, _topic_to_address, safe_block_number

logger = logging.getLogger(__name__)

TRANSFER_SELECTOR = 'a9059cbb'  # transfer(address,uint256)
STUCK_AFTER = timedelta(minutes=30)
REBROADCAST_GAS_BUMP = Decimal('1.125')  # nodes require >= 10% bump to replace a pending tx
MAX_GAS_PRICE_MULTIPLIER = 2  # never rebroadcast above 2x the current network gas price
GAS_ESTIMATE_MARGIN = Decimal('1.2')  # headroom over eth_estimateGas


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


def is_valid_address(address) -> bool:
    """A 20-byte hex address; if it is mixed case, its EIP-55 checksum must be right (catches typos).

    web3.py 7 / eth-utils 6 ``is_address`` no longer checks the checksum of mixed-case addresses.
    """
    from web3 import Web3
    if not isinstance(address, str) or not Web3.is_address(address):
        return False
    body = address[2:]
    if body.lower() == body or body.upper() == body:
        return True
    return Web3.is_checksum_address(address)


def _checksum(address: str) -> str:
    from web3 import Web3
    if not is_valid_address(address):
        raise InvalidAddressException()
    return Web3.to_checksum_address(address)


def _encode_erc20_transfer(to: str, amount: int) -> str:
    from eth_abi import encode
    return '0x' + TRANSFER_SELECTOR + encode(['address', 'uint256'], [to, amount]).hex()


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


def _today_total(user, coin_info) -> int:
    """Everything the user sent out today: withdrawals (not rejected/failed) and internal transfers.
    Daily limits and the auto-approve threshold apply to this total."""
    from .models import InternalTransfer
    today = timezone.localdate()
    withdrawn = Withdrawal.objects.filter(user=user, coin_info=coin_info, created_at__date=today) \
        .exclude(status__in=[Withdrawal.Status.REJECTED, Withdrawal.Status.FAILED]) \
        .aggregate(s=Sum('amount'))['s'] or 0
    transferred = InternalTransfer.objects.filter(sender=user, coin_info=coin_info, created_at__date=today) \
        .aggregate(s=Sum('amount'))['s'] or 0
    return int(withdrawn) + int(transferred)


def would_auto_approve(user, coin_info, amount: int) -> bool:
    """True if a withdrawal of this amount would skip admin review."""
    limit = int(coin_info.auto_approve_withdrawal_limit or 0)
    return limit > 0 and _today_total(user, coin_info) + amount <= limit


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
        own_addresses = {a.lower() for a in CryptoAddress.objects.filter(user=user).values_list('address', flat=True)}
        if not own_addresses:
            raise InsufficientBalanceException()
        if to_address.lower() in own_addresses:
            raise InvalidAddressException()

        # Older accounts may have several addresses: debit one that holds enough.
        # of=('self',): lock only the balance row, not the joined address row.
        balance = CoinInfoBalance.objects.select_for_update(of=('self',)).select_related('crypto_address') \
            .filter(crypto_address__user=user, coin_info=coin_info) \
            .annotate(total=F('internal_amount') + F('external_amount')) \
            .filter(total__gte=amount).order_by('crypto_address_id').first()
        if balance is None:
            raise InsufficientBalanceException()

        today_total = _today_total(user, coin_info)
        if coin_info.daily_withdrawal_limit > 0 and today_total + amount > coin_info.daily_withdrawal_limit:
            raise DailyLimitExceededException()

        CoinInfoBalance.objects.filter(pk=balance.pk).update(external_amount=F('external_amount') - amount)

        log = TransactionLog.objects.create(
            blockchain_id=coin_info.blockchain_id, coin_info=coin_info, user=user,
            direction=TransactionLog.Direction.WITHDRAWAL, status=TransactionLog.Status.PENDING,
            recipient_address=to_address, amount=amount,
        )
        # The auto-approve limit applies to the day's total, so splitting does not bypass review.
        auto = coin_info.auto_approve_withdrawal_limit > 0 and \
            today_total + amount <= coin_info.auto_approve_withdrawal_limit
        return Withdrawal.objects.create(
            user=user, crypto_address=balance.crypto_address, coin_info=coin_info,
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
    """Reject and refund. Only possible before a transaction was ever signed for it."""
    with transaction.atomic():
        w = Withdrawal.objects.select_for_update().get(pk=w.pk)
        if w.status not in (Withdrawal.Status.PENDING_REVIEW, Withdrawal.Status.APPROVED):
            return False
        if w.nonce is not None or w.tx_id or w.previous_tx_ids:
            return False
        w.reviewed_by = reviewer
        w.reviewed_at = timezone.now()
        _refund(w, Withdrawal.Status.REJECTED, reason)
    return True


# --------------------------------------------------------------------------- broadcast
def _transfer_call(w: Withdrawal) -> dict:
    """to / value / data of the transfer, shared by gas estimation and signing."""
    if w.coin_info.contract:
        return {'to': _checksum(w.coin_info.contract), 'value': 0,
                'data': _encode_erc20_transfer(_checksum(w.to_address), int(w.amount))}
    return {'to': _checksum(w.to_address), 'value': int(w.amount)}


def _min_gas(w: Withdrawal) -> int:
    bc = w.coin_info.blockchain
    return bc.gas_limit_erc20 if w.coin_info.contract else bc.gas_limit


def _estimate_gas(w3, w: Withdrawal, sender: str) -> int:
    """eth_estimateGas plus a margin, never below the chain's configured minimum.

    A fixed limit is not safe: on Sepolia a plain ETH transfer to a never-used
    address costs ~207k gas (21k to an existing account), so a fixed 21000 limit
    runs out of gas and the withdrawal fails on-chain.
    """
    estimate = int(w3.eth.estimate_gas({'from': _checksum(sender), **_transfer_call(w)}))
    return max(_min_gas(w), math.ceil(Decimal(estimate) * GAS_ESTIMATE_MARGIN))


def _build_tx(w: Withdrawal, nonce: int, gas_price: int, gas: int) -> dict:
    bc = w.coin_info.blockchain
    if not bc.chain_id:
        raise BroadcastError(f'{bc} has no chain_id; refusing to sign without replay protection')
    return {'nonce': nonce, 'gasPrice': gas_price, 'chainId': bc.chain_id, 'gas': gas, **_transfer_call(w)}


def _sign(tx: dict, hot: HotWallet):
    from eth_account import Account
    return Account.sign_transaction(tx, hot.get_private_key())


def _send(w3, w: Withdrawal, signed) -> bool:
    """Send a signed tx. Errors are only recorded: the tx may still have reached the network."""
    try:
        w3.eth.send_raw_transaction(signed.raw_transaction)
        return True
    except Exception as e:
        if 'already known' in str(e).lower():
            return True
        logger.warning('Withdrawal #%s: send failed, will retry with the same nonce: %s', w.pk, e)
        Withdrawal.objects.filter(pk=w.pk).update(error_message=str(e)[:2000])
        return False


def broadcast(w: Withdrawal, w3=None) -> bool:
    """Assign a nonce to an approved withdrawal, sign it with the hot wallet and send it."""
    bc = w.coin_info.blockchain
    w3 = _get_w3(bc, w3)

    with transaction.atomic():
        # of=('self',): PostgreSQL refuses FOR UPDATE on the nullable side of an outer join.
        w = Withdrawal.objects.select_for_update(of=('self',)).select_related('coin_info__blockchain').get(pk=w.pk)
        if w.status != Withdrawal.Status.APPROVED:
            return False
        hot = HotWallet.objects.select_for_update().filter(blockchain=bc).first()
        if hot is None:
            raise BroadcastError(f'{bc} has no hot wallet')

        try:
            gas = _estimate_gas(w3, w, hot.address)
        except Exception as e:
            # Nothing is signed and no nonce is used yet: keep it approved and retry on the next run
            # (e.g. the hot wallet does not hold enough of the token yet).
            logger.warning('Withdrawal #%s: gas estimation failed, will retry: %s', w.pk, e)
            Withdrawal.objects.filter(pk=w.pk).update(error_message=f'Gas estimation failed: {e}'[:2000])
            return False

        nonce = max(int(w3.eth.get_transaction_count(_checksum(hot.address), 'pending')), hot.next_nonce)
        gas_price = int(w3.eth.gas_price)
        signed = _sign(_build_tx(w, nonce, gas_price, gas), hot)
        tx_id = _to_hex(signed.hash)

        # Persist the hash and nonce before sending so they can never be lost.
        w.tx_id, w.nonce, w.gas_price, w.gas_limit = tx_id, nonce, gas_price, gas
        w.status, w.broadcast_at, w.error_message = Withdrawal.Status.BROADCAST, timezone.now(), ''
        w.save()
        TransactionLog.objects.filter(pk=w.transaction_log_id).update(
            tx_id=tx_id, nonce=nonce, sender_address=hot.address)
        hot.next_nonce = nonce + 1
        hot.save(update_fields=['next_nonce'])

    sent = _send(w3, w, signed)
    if sent:
        logger.info('Withdrawal #%s broadcast as %s (nonce %s)', w.pk, tx_id, nonce)
    return sent


def rebroadcast(w: Withdrawal, w3=None) -> bool:
    """Re-send a stuck withdrawal with the same nonce and a higher (capped) gas price."""
    bc = w.coin_info.blockchain
    w3 = _get_w3(bc, w3)
    with transaction.atomic():
        w = Withdrawal.objects.select_for_update().get(pk=w.pk)
        if w.status not in (Withdrawal.Status.STUCK, Withdrawal.Status.BROADCAST) or w.nonce is None:
            return False
        hot = HotWallet.objects.get(blockchain=bc)
        current = int(w3.eth.gas_price)
        bumped = math.ceil(Decimal(w.gas_price or 0) * REBROADCAST_GAS_BUMP) + 1
        gas_price = max(current, min(bumped, current * MAX_GAS_PRICE_MULTIPLIER))
        # Same nonce, same gas limit as the original; only the gas price goes up.
        gas = int(w.gas_limit) if w.gas_limit else _min_gas(w)
        signed = _sign(_build_tx(w, int(w.nonce), gas_price, gas), hot)
        tx_id = _to_hex(signed.hash)
        if tx_id != w.tx_id:
            w.previous_tx_ids = list(w.previous_tx_ids or []) + [w.tx_id]
            w.tx_id, w.gas_price = tx_id, gas_price
        w.status, w.broadcast_at, w.error_message = Withdrawal.Status.BROADCAST, timezone.now(), ''
        w.save()
        TransactionLog.objects.filter(pk=w.transaction_log_id).update(tx_id=w.tx_id)
    return _send(w3, w, signed)


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


def rebroadcast_stuck(blockchain, w3=None) -> int:
    done = 0
    for w in Withdrawal.objects.filter(coin_info__blockchain=blockchain, status=Withdrawal.Status.STUCK).order_by('nonce'):
        done += int(rebroadcast(w, w3))
    return done


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


def _token_transfer_happened(w: Withdrawal, hot_address: str, receipt) -> bool:
    """Some tokens return false instead of reverting; require the expected Transfer log."""
    contract = w.coin_info.contract.lower()
    for log in receipt.get('logs') or []:
        topics = log.get('topics') or []
        if str(log.get('address', '')).lower() != contract or len(topics) != 3:
            continue
        if _to_hex(topics[0]) != TRANSFER_TOPIC:
            continue
        if _topic_to_address(topics[1]) == hot_address.lower() \
                and _topic_to_address(topics[2]) == w.to_address.lower() \
                and _to_int(log.get('data') or 0) == int(w.amount):
            return True
    return False


def check_broadcast_withdrawals(blockchain, w3=None, stuck_after: Optional[timedelta] = None) -> dict:
    """Poll receipts of broadcast withdrawals and finalise them."""
    w3 = _get_w3(blockchain, w3)
    stuck_after = stuck_after or STUCK_AFTER
    try:
        safe_block = safe_block_number(blockchain, w3)
    except ScanError as e:
        raise BroadcastError(str(e)) from e
    hot = HotWallet.objects.filter(blockchain=blockchain).first()
    final_nonce = None  # hot wallet nonce count at the safe block, fetched lazily
    result = {'confirmed': 0, 'failed': 0, 'stuck': 0, 'requeued': 0}

    qs = Withdrawal.objects.filter(coin_info__blockchain=blockchain,
                                   status__in=[Withdrawal.Status.BROADCAST, Withdrawal.Status.STUCK])
    for w in qs:
        hashes = [w.tx_id] + list(w.previous_tx_ids or [])
        if hot is not None and final_nonce is None:
            final_nonce = int(w3.eth.get_transaction_count(_checksum(hot.address), safe_block))
        mined_hash, receipt = _find_receipt(w3, hashes)

        if receipt is None:
            if (w.status == Withdrawal.Status.STUCK and hot is not None and w.nonce is not None
                    and final_nonce > w.nonce and _find_receipt(w3, hashes)[1] is None):
                # Stuck for a while, the nonce was used in a final block, and (checked twice)
                # none of our hashes has a receipt: another transaction took the nonce, so ours
                # can never be mined. Safe to queue again with a new nonce.
                with transaction.atomic():
                    updated = Withdrawal.objects.filter(pk=w.pk, status=w.status, tx_id=w.tx_id).update(
                        status=Withdrawal.Status.APPROVED, previous_tx_ids=hashes, tx_id=None, nonce=None,
                        gas_price=None, broadcast_at=None,
                        error_message=f'Nonce {w.nonce} was used by another transaction; queued again')
                    TransactionLog.objects.filter(pk=w.transaction_log_id).update(tx_id=None, nonce=None)
                result['requeued'] += updated
            elif w.status == Withdrawal.Status.BROADCAST and w.broadcast_at and w.broadcast_at < timezone.now() - stuck_after:
                Withdrawal.objects.filter(pk=w.pk, status=Withdrawal.Status.BROADCAST).update(status=Withdrawal.Status.STUCK)
                result['stuck'] += 1
            continue

        block_number = int(receipt['blockNumber'])
        if block_number > safe_block:
            continue

        with transaction.atomic():
            w = Withdrawal.objects.select_for_update().get(pk=w.pk)
            if w.status not in (Withdrawal.Status.BROADCAST, Withdrawal.Status.STUCK):
                continue
            w.tx_id = mined_hash
            succeeded = int(receipt['status']) == 1
            reason = 'Transaction reverted on chain'
            if succeeded and w.coin_info.contract and not _token_transfer_happened(w, hot.address if hot else '', receipt):
                succeeded = False
                reason = 'Token contract did not emit the expected Transfer event'
            if succeeded:
                w.status = Withdrawal.Status.CONFIRMED
                w.save()
                TransactionLog.objects.filter(pk=w.transaction_log_id).update(
                    status=TransactionLog.Status.CONFIRMED, tx_id=mined_hash, block_number=block_number)
                result['confirmed'] += 1
            else:
                _refund(w, Withdrawal.Status.FAILED, reason)
                TransactionLog.objects.filter(pk=w.transaction_log_id).update(tx_id=mined_hash, block_number=block_number)
                result['failed'] += 1
    return result


def process_chain_withdrawals(blockchain, w3=None) -> dict:
    """One full pass: confirm/finalise, rebroadcast stuck ones, then send newly approved ones."""
    w3 = _get_w3(blockchain, w3)
    checked = check_broadcast_withdrawals(blockchain, w3)
    rebroadcast_count = rebroadcast_stuck(blockchain, w3)
    sent = broadcast_approved(blockchain, w3)
    return {**checked, 'rebroadcast': rebroadcast_count, 'sent': sent}
