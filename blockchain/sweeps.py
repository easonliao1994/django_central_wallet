"""Sweeping deposit addresses into the hot wallet (or the cold wallet).

Deposits stay on each user's deposit address, while withdrawals are paid from the
hot wallet, so funds are periodically moved ("swept") from deposit addresses.

pending --(token, not enough gas)--> topping_up --top-up mined--> pending
   |                                      \\--top-up reverted--> failed
   +--(enough gas / native coin)--> broadcast --final receipt ok--> confirmed
   |                                      \\--final receipt failed--> failed
   +--(balance below threshold after fees)--> cancelled

Rules
-----
* Only one open sweep per deposit address (database constraint): sweeps share the
  address nonce and its native balance (gas). Token sweeps are created before native
  ones, so the native coin on an address is not swept away while a token needs gas.
* Native coin sweeps send the balance minus the gas fee. Token sweeps send the whole
  token balance; if the address lacks native coin for gas, the hot wallet first sends a
  gas top-up and the sweep is signed once the top-up is mined.
* Each leg (top-up, sweep) gets its nonce once and keeps it. A leg not mined in time is
  rebroadcast with the same nonce and a higher gas price, so it can never be paid twice
  and never leaves a nonce gap in the hot wallet (which would block withdrawals).
* Hashes and nonces are committed before sending; send errors are only recorded.
* Confirmation uses the receipt at the chain's safe block. Token sweeps also require the
  expected Transfer log. The amount actually moved (and the gas fee) are recorded on the
  TransactionLog so balance reconciliation stays exact.
* Sweeps never touch users' platform balances (CoinInfoBalance).
"""
import logging
import math
from datetime import timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Blockchain, CoinInfo, CryptoAddress, HotWallet, Sweep, TransactionLog
from .scanner import TRANSFER_TOPIC, ScanError, _to_hex, _to_int, _topic_to_address, safe_block_number
from .withdrawals import (
    GAS_ESTIMATE_MARGIN, MAX_GAS_PRICE_MULTIPLIER, REBROADCAST_GAS_BUMP, STUCK_AFTER,
    BroadcastError, _checksum, _encode_erc20_transfer, _find_receipt, _get_w3,
)

logger = logging.getLogger(__name__)

BALANCE_OF_SELECTOR = '70a08231'  # balanceOf(address)
TOPUP_GAS_PRICE_BUFFER = 2  # top up enough native coin for twice the current gas price
SIGN_GAS_HEADROOM = Decimal('1.5')  # token sweeps need gas for 1.5x the price, so one rebroadcast bump fits
MAX_TOPUPS = 3  # per sweep, in case gas prices keep rising
MAX_SEND_ATTEMPTS = 10  # failed sign/send attempts before a pending sweep is cancelled
RETRY_COOLDOWN = timedelta(hours=6)  # wait before sweeping an address/coin again after a failure or cancel
NEW_SWEEPS_PER_RUN = 20

# Fields of each leg on the Sweep model.
LEG_PREFIX = {'sweep': '', 'topup': 'topup_'}


# --------------------------------------------------------------------------- balances
def token_balance(w3, contract: str, address: str) -> int:
    data = '0x' + BALANCE_OF_SELECTOR + '0' * 24 + _checksum(address)[2:].lower()
    return _to_int(w3.eth.call({'to': _checksum(contract), 'data': data}))


def coin_balance(w3, coin_info: CoinInfo, address: str) -> int:
    if coin_info.contract:
        return token_balance(w3, coin_info.contract, address)
    return int(w3.eth.get_balance(_checksum(address)))


def _estimate(w3, sender: str, call: dict, minimum: int) -> int:
    estimate = int(w3.eth.estimate_gas({'from': _checksum(sender), **call}))
    return max(minimum, math.ceil(Decimal(estimate) * GAS_ESTIMATE_MARGIN))


def _token_call(coin_info: CoinInfo, to: str, amount: int) -> dict:
    return {'to': _checksum(coin_info.contract), 'value': 0,
            'data': _encode_erc20_transfer(_checksum(to), int(amount))}


def _sign(tx: dict, private_key: str):
    from eth_account import Account
    return Account.sign_transaction(tx, private_key)


def _send(w3, s: Sweep, signed) -> bool:
    """Send a signed tx. Errors are only recorded: the tx may still have reached the network."""
    try:
        w3.eth.send_raw_transaction(signed.raw_transaction)
        return True
    except Exception as e:
        if 'already known' in str(e).lower():
            return True
        logger.warning('Sweep #%s: send failed, will retry with the same nonce: %s', s.pk, e)
        Sweep.objects.filter(pk=s.pk).update(error_message=str(e)[:2000])
        return False


# --------------------------------------------------------------------------- candidates
def sweep_destination(blockchain: Blockchain, coin_info: CoinInfo, hot: HotWallet, w3) -> str:
    """Hot wallet, or the cold wallet once the hot wallet holds at least hot_wallet_limit."""
    cold = (blockchain.cold_wallet_address or '').strip()
    if cold and coin_info.hot_wallet_limit and coin_info.hot_wallet_limit > 0:
        from .withdrawals import is_valid_address
        if not is_valid_address(cold):
            logger.error('%s: cold_wallet_address %r is not a valid address; sweeping to the hot wallet', blockchain, cold)
        elif coin_balance(w3, coin_info, hot.address) >= int(coin_info.hot_wallet_limit):
            return _checksum(cold)
    return _checksum(hot.address)


def find_candidates(blockchain: Blockchain, w3) -> List[Tuple[CryptoAddress, CoinInfo, int]]:
    """(address, coin, on-chain balance) pairs at or above the coin's sweep threshold.

    The transaction log narrows the search to addresses expected to hold enough; the
    on-chain balance is the final check. Tokens come first so that an address's native
    coin is not swept while one of its tokens still needs it for gas.
    """
    from .reconciliation import expected_balances

    coin_infos = list(CoinInfo.objects.filter(blockchain=blockchain, coin__isnull=False, sweep_threshold__gt=0))
    coin_infos.sort(key=lambda ci: 0 if ci.contract else 1)
    busy = set(Sweep.objects.filter(blockchain=blockchain, status__in=Sweep.OPEN_STATUSES)
               .values_list('crypto_address_id', flat=True))
    cooling_down = set(Sweep.objects.filter(
        blockchain=blockchain, status__in=[Sweep.Status.FAILED, Sweep.Status.CANCELLED],
        updated_at__gte=timezone.now() - RETRY_COOLDOWN).values_list('crypto_address_id', 'coin_info_id'))
    addresses = {a.address.lower(): a for a in CryptoAddress.objects.all()}
    # A native sweep pays its own gas: only worth it if the threshold is left after the fee.
    native_fee = math.ceil(Decimal(blockchain.gas_limit) * GAS_ESTIMATE_MARGIN) * int(w3.eth.gas_price)

    result = []
    for coin_info in coin_infos:
        threshold = int(coin_info.sweep_threshold)
        fee = 0 if coin_info.contract else native_fee
        for address, expected in sorted(expected_balances(blockchain, coin_info).items()):
            crypto_address = addresses.get(address)
            if crypto_address is None or crypto_address.pk in busy or expected - fee < threshold \
                    or (crypto_address.pk, coin_info.pk) in cooling_down:
                continue
            try:
                balance = coin_balance(w3, coin_info, crypto_address.address)
            except Exception as e:
                logger.warning('%s: balance of %s %s unavailable, skipped: %s', blockchain, coin_info, address, e)
                continue
            if balance - fee >= threshold:
                result.append((crypto_address, coin_info, balance))
                busy.add(crypto_address.pk)
    return result


def create_sweeps(blockchain: Blockchain, w3, hot: HotWallet, limit: int = NEW_SWEEPS_PER_RUN) -> int:
    created = 0
    for crypto_address, coin_info, _balance in find_candidates(blockchain, w3)[:limit]:
        try:
            with transaction.atomic():
                Sweep.objects.create(blockchain=blockchain, coin_info=coin_info, crypto_address=crypto_address,
                                     to_address=sweep_destination(blockchain, coin_info, hot, w3))
            created += 1
        except IntegrityError:
            continue  # an open sweep for this address appeared meanwhile
    return created


# --------------------------------------------------------------------------- sending
def _cancel(s: Sweep, reason: str):
    Sweep.objects.filter(pk=s.pk, status=Sweep.Status.PENDING).update(status=Sweep.Status.CANCELLED, error_message=reason)


def _send_topup(s: Sweep, w3, hot: HotWallet, amount: int) -> bool:
    bc = s.blockchain
    native = bc.get_native_coin_info()
    if native is None:
        raise BroadcastError(f'{bc} has no native CoinInfo; cannot record gas top-ups')
    address = s.crypto_address.address
    call = {'to': _checksum(address), 'value': int(amount)}
    gas = _estimate(w3, hot.address, call, bc.gas_limit)

    with transaction.atomic():
        s = Sweep.objects.select_for_update().get(pk=s.pk)
        if s.status != Sweep.Status.PENDING:
            return False
        hot = HotWallet.objects.select_for_update().get(pk=hot.pk)
        gas_price = int(w3.eth.gas_price)
        # Never reserve a hot wallet nonce for a top-up that cannot be paid: a rejected
        # transaction would leave a nonce gap that blocks every later withdrawal.
        available = int(w3.eth.get_balance(_checksum(hot.address), 'pending'))
        if available < int(amount) + gas * gas_price:
            raise ValueError(f'Hot wallet has {available} wei pending balance, needs {int(amount) + gas * gas_price} '
                             f'for the gas top-up')
        nonce = max(int(w3.eth.get_transaction_count(_checksum(hot.address), 'pending')), hot.next_nonce)
        signed = _sign({'nonce': nonce, 'gasPrice': gas_price, 'chainId': bc.chain_id, 'gas': gas, **call},
                       hot.get_private_key())
        tx_id = _to_hex(signed.hash)
        log = TransactionLog.objects.create(
            tx_id=tx_id, nonce=nonce, blockchain=bc, coin_info=native, direction=TransactionLog.Direction.GAS_TOPUP,
            status=TransactionLog.Status.PENDING, sender_address=hot.address.lower(), recipient_address=address.lower(),
            amount=int(amount))
        s.topup_tx_id, s.topup_previous_tx_ids, s.topup_nonce = tx_id, [], nonce
        s.topup_gas_price, s.topup_gas_limit, s.topup_amount = gas_price, gas, int(amount)
        s.topup_broadcast_at, s.topup_count, s.topup_log = timezone.now(), s.topup_count + 1, log
        s.status, s.error_message = Sweep.Status.TOPPING_UP, ''
        s.save()
        hot.next_nonce = nonce + 1
        hot.save(update_fields=['next_nonce'])

    # Persisted: from here on the top-up is tracked (and rebroadcast) even if this send fails.
    if _send(w3, s, signed):
        logger.info('Sweep #%s: gas top-up of %s sent as %s (hot wallet nonce %s)', s.pk, amount, tx_id, nonce)
    return True


def _send_sweep(s: Sweep, w3, hot: HotWallet) -> str:
    """Sign and send the sweep, or top up gas first. Returns 'sent', 'topup', 'cancelled' or 'failed'."""
    bc, coin_info = s.blockchain, s.coin_info
    if not bc.chain_id:
        raise BroadcastError(f'{bc} has no chain_id; refusing to sign without replay protection')
    address = s.crypto_address.address
    threshold = int(coin_info.sweep_threshold)
    native_balance = int(w3.eth.get_balance(_checksum(address)))
    gas_price = int(w3.eth.gas_price)

    if coin_info.contract:
        amount = token_balance(w3, coin_info.contract, address)
        if amount <= 0 or amount < threshold:
            _cancel(s, f'Token balance {amount} is below the sweep threshold')
            return 'cancelled'
        call = _token_call(coin_info, s.to_address, amount)
        gas = _estimate(w3, address, call, bc.gas_limit_erc20)
        # Keep headroom so a rebroadcast with a bumped gas price can still be paid.
        if native_balance < math.ceil(gas * gas_price * SIGN_GAS_HEADROOM):
            if s.topup_count >= MAX_TOPUPS:
                Sweep.objects.filter(pk=s.pk).update(status=Sweep.Status.FAILED,
                                                     error_message=f'Still not enough gas after {MAX_TOPUPS} top-ups')
                return 'failed'
            return 'topup' if _send_topup(s, w3, hot, gas * gas_price * TOPUP_GAS_PRICE_BUFFER - native_balance) else 'failed'
    else:
        # value does not change the gas of a plain transfer; estimate with a token amount.
        gas = _estimate(w3, address, {'to': _checksum(s.to_address), 'value': 1}, bc.gas_limit)
        amount = native_balance - gas * gas_price
        if amount <= 0 or amount < threshold:
            _cancel(s, f'Balance after gas ({amount}) is below the sweep threshold')
            return 'cancelled'
        call = {'to': _checksum(s.to_address), 'value': amount}

    with transaction.atomic():
        s = Sweep.objects.select_for_update().get(pk=s.pk)
        if s.status != Sweep.Status.PENDING:
            return 'failed'
        nonce = int(w3.eth.get_transaction_count(_checksum(address), 'pending'))
        signed = _sign({'nonce': nonce, 'gasPrice': gas_price, 'chainId': bc.chain_id, 'gas': gas, **call},
                       s.crypto_address.get_private_key())
        tx_id = _to_hex(signed.hash)
        log = TransactionLog.objects.create(
            tx_id=tx_id, nonce=nonce, blockchain=bc, coin_info=coin_info, direction=TransactionLog.Direction.SWEEP,
            status=TransactionLog.Status.PENDING, sender_address=address.lower(),
            recipient_address=s.to_address.lower(), amount=amount)
        s.tx_id, s.previous_tx_ids, s.nonce, s.gas_price, s.gas_limit = tx_id, [], nonce, gas_price, gas
        s.amount, s.broadcast_at, s.transaction_log = amount, timezone.now(), log
        s.status, s.error_message = Sweep.Status.BROADCAST, ''
        s.save()

    if _send(w3, s, signed):
        logger.info('Sweep #%s: %s %s from %s to %s sent as %s', s.pk, amount, coin_info, address, s.to_address, tx_id)
    return 'sent'


def send_pending(blockchain: Blockchain, w3, hot: HotWallet) -> Dict[str, int]:
    result = {'sent': 0, 'topups': 0, 'cancelled': 0}
    for s in Sweep.objects.filter(blockchain=blockchain, status=Sweep.Status.PENDING).select_related(
            'coin_info', 'crypto_address', 'blockchain').order_by('id'):
        try:
            outcome = _send_sweep(s, w3, hot)
        except BroadcastError:
            raise
        except Exception as e:
            # e.g. gas estimation failed: nothing signed, keep it pending and retry next run,
            # but give up after a while so the address is not blocked forever.
            attempts = s.send_attempts + 1
            if attempts >= MAX_SEND_ATTEMPTS:
                logger.error('Sweep #%s: cancelled after %s failed attempts: %s', s.pk, attempts, e)
                Sweep.objects.filter(pk=s.pk, status=Sweep.Status.PENDING).update(
                    status=Sweep.Status.CANCELLED, send_attempts=attempts,
                    error_message=f'Cancelled after {attempts} failed attempts: {e}'[:2000])
                result['cancelled'] += 1
            else:
                logger.warning('Sweep #%s: not sent, will retry: %s', s.pk, e)
                Sweep.objects.filter(pk=s.pk).update(send_attempts=attempts, error_message=f'Not sent: {e}'[:2000])
            continue
        if outcome == 'sent':
            result['sent'] += 1
        elif outcome == 'topup':
            result['topups'] += 1
        elif outcome == 'cancelled':
            result['cancelled'] += 1
    return result


# --------------------------------------------------------------------------- rebroadcast
def _rebroadcast(s: Sweep, leg: str, w3, hot: HotWallet) -> bool:
    """Re-sign a leg that was not mined in time: same nonce, same gas limit, higher gas price."""
    p = LEG_PREFIX[leg]
    bc = s.blockchain
    current = int(w3.eth.gas_price)
    old_price = int(getattr(s, p + 'gas_price') or 0)
    bumped = math.ceil(Decimal(old_price) * REBROADCAST_GAS_BUMP) + 1
    gas_price = max(current, min(bumped, current * MAX_GAS_PRICE_MULTIPLIER))
    gas = int(getattr(s, p + 'gas_limit'))
    nonce = int(getattr(s, p + 'nonce'))

    if leg == 'topup':
        call = {'to': _checksum(s.crypto_address.address), 'value': int(s.topup_amount)}
        key = hot.get_private_key()
        if int(w3.eth.get_balance(_checksum(hot.address), 'pending')) < call['value'] + gas * gas_price:
            Sweep.objects.filter(pk=s.pk).update(error_message='Hot wallet cannot pay the top-up rebroadcast; fund it')
            return False
    elif s.coin_info.contract:
        call = _token_call(s.coin_info, s.to_address, int(s.amount))
        key = s.crypto_address.get_private_key()
        if int(w3.eth.get_balance(_checksum(s.crypto_address.address))) < gas * gas_price:
            Sweep.objects.filter(pk=s.pk).update(
                error_message='Not enough gas on the address to rebroadcast at a higher price; waiting')
            return False
    else:
        # The pending transaction was not mined, so the balance is unchanged; pay the new fee from it.
        amount = int(w3.eth.get_balance(_checksum(s.crypto_address.address))) - gas * gas_price
        if amount <= 0:
            return False
        call = {'to': _checksum(s.to_address), 'value': amount}
        key = s.crypto_address.get_private_key()

    signed = _sign({'nonce': nonce, 'gasPrice': gas_price, 'chainId': bc.chain_id, 'gas': gas, **call}, key)
    tx_id = _to_hex(signed.hash)
    with transaction.atomic():
        locked = Sweep.objects.select_for_update().get(pk=s.pk)
        if locked.status != s.status or getattr(locked, p + 'tx_id') != getattr(s, p + 'tx_id'):
            return False
        if tx_id != getattr(locked, p + 'tx_id'):
            setattr(locked, p + 'previous_tx_ids', list(getattr(locked, p + 'previous_tx_ids') or []) + [getattr(locked, p + 'tx_id')])
            setattr(locked, p + 'tx_id', tx_id)
            setattr(locked, p + 'gas_price', gas_price)
        setattr(locked, p + 'broadcast_at', timezone.now())
        log_id = locked.topup_log_id if leg == 'topup' else locked.transaction_log_id
        if leg == 'sweep' and not s.coin_info.contract:
            locked.amount = call['value']
            TransactionLog.objects.filter(pk=log_id).update(tx_id=tx_id, amount=call['value'])
        else:
            TransactionLog.objects.filter(pk=log_id).update(tx_id=tx_id)
        locked.save()
    logger.info('Sweep #%s: %s leg rebroadcast as %s (nonce %s, gas price %s)', s.pk, leg, tx_id, nonce, gas_price)
    return _send(w3, locked, signed)


# --------------------------------------------------------------------------- confirmation
def _token_amount_swept(s: Sweep, receipt) -> Optional[int]:
    """Total the token contract reports moving from the deposit address to the destination,
    or None if it emitted no such Transfer (some tokens return false instead of reverting)."""
    contract = s.coin_info.contract.lower()
    total = None
    for log in receipt.get('logs') or []:
        topics = log.get('topics') or []
        if str(log.get('address', '')).lower() != contract or len(topics) != 3 or _to_hex(topics[0]) != TRANSFER_TOPIC:
            continue
        if _topic_to_address(topics[1]) == s.crypto_address.address.lower() \
                and _topic_to_address(topics[2]) == s.to_address.lower():
            total = (total or 0) + _to_int(log.get('data') or 0)
    return total


def _fee(w3, mined_hash: str, receipt) -> Tuple[int, dict]:
    tx = w3.eth.get_transaction(mined_hash)
    price = receipt.get('effectiveGasPrice')
    price = int(price if price is not None else tx['gasPrice'])
    return int(receipt['gasUsed']) * price, tx


def check_sweeps(blockchain: Blockchain, w3, hot: Optional[HotWallet], stuck_after: Optional[timedelta] = None) -> Dict[str, int]:
    """Finalise mined legs and rebroadcast ones that are not mined in time."""
    stuck_after = stuck_after or STUCK_AFTER
    try:
        safe_block = safe_block_number(blockchain, w3)
    except ScanError as e:
        raise BroadcastError(str(e)) from e
    result = {'confirmed': 0, 'failed': 0, 'topped_up': 0, 'rebroadcast': 0}

    for s in Sweep.objects.filter(blockchain=blockchain, status__in=[Sweep.Status.TOPPING_UP, Sweep.Status.BROADCAST]) \
            .select_related('coin_info', 'crypto_address', 'blockchain'):
        try:
            _check_one(s, w3, hot, safe_block, stuck_after, result)
        except Exception as e:
            logger.exception('Sweep #%s: check failed, will retry: %s', s.pk, e)
    return result


def _check_one(s: Sweep, w3, hot: Optional[HotWallet], safe_block: int, stuck_after: timedelta, result: dict):
    """Finalise or rebroadcast one in-flight sweep leg."""
    leg = 'topup' if s.status == Sweep.Status.TOPPING_UP else 'sweep'
    p = LEG_PREFIX[leg]
    hashes = [getattr(s, p + 'tx_id')] + list(getattr(s, p + 'previous_tx_ids') or [])
    mined_hash, receipt = _find_receipt(w3, hashes)

    if receipt is None:
        broadcast_at = getattr(s, p + 'broadcast_at')
        if broadcast_at and broadcast_at < timezone.now() - stuck_after and hot is not None:
            sender = hot.address if leg == 'topup' else s.crypto_address.address
            final_nonce = int(w3.eth.get_transaction_count(_checksum(sender), safe_block))
            if final_nonce > int(getattr(s, p + 'nonce')) and _find_receipt(w3, hashes)[1] is None:
                # Only our own transactions use these nonces, so this should not happen; stop and flag it
                # for an admin (the reconciliation report shows whether anything actually moved).
                with transaction.atomic():
                    if Sweep.objects.filter(pk=s.pk, status=s.status).update(
                            status=Sweep.Status.FAILED,
                            error_message=f'{leg} nonce was used by a transaction we do not know; check the explorer'):
                        log_id = s.topup_log_id if leg == 'topup' else s.transaction_log_id
                        TransactionLog.objects.filter(pk=log_id).update(status=TransactionLog.Status.FAILED)
                result['failed'] += 1
            else:
                result['rebroadcast'] += int(_rebroadcast(s, leg, w3, hot))
        return

    block_number = int(receipt['blockNumber'])
    if block_number > safe_block:
        return
    fee, tx = _fee(w3, mined_hash, receipt)
    succeeded = int(receipt['status']) == 1

    with transaction.atomic():
        locked = Sweep.objects.select_for_update().get(pk=s.pk)
        if locked.status != s.status:
            return
        setattr(locked, p + 'tx_id', mined_hash)
        log_id = locked.topup_log_id if leg == 'topup' else locked.transaction_log_id
        moved = int(tx['value'])
        if leg == 'topup':
            TransactionLog.objects.filter(pk=log_id).update(
                tx_id=mined_hash, block_number=block_number, fee=fee, amount=moved,
                status=TransactionLog.Status.CONFIRMED if succeeded else TransactionLog.Status.FAILED)
            if succeeded:
                locked.status = Sweep.Status.PENDING  # signed on this run's send pass
                result['topped_up'] += 1
            else:
                locked.status, locked.error_message = Sweep.Status.FAILED, 'Gas top-up reverted on chain'
                result['failed'] += 1
        else:
            amount = moved
            if s.coin_info.contract:
                amount = int(locked.amount)
                if succeeded:
                    swept = _token_amount_swept(locked, receipt)
                    if swept is None:
                        succeeded = False
                        locked.error_message = 'Token contract did not emit the expected Transfer event'
                    elif swept != amount:
                        # e.g. fee-on-transfer tokens: record what actually moved and flag it.
                        locked.error_message = f'Transfer event moved {swept}, not the {amount} sent; check the token'
                        amount = swept
            TransactionLog.objects.filter(pk=log_id).update(
                tx_id=mined_hash, block_number=block_number, fee=fee, amount=amount,
                status=TransactionLog.Status.CONFIRMED if succeeded else TransactionLog.Status.FAILED)
            locked.amount = amount
            if succeeded:
                locked.status = Sweep.Status.CONFIRMED
                result['confirmed'] += 1
            else:
                locked.status = Sweep.Status.FAILED
                locked.error_message = locked.error_message or 'Sweep reverted on chain'
                result['failed'] += 1
        locked.save()


# --------------------------------------------------------------------------- entry point
def process_chain_sweeps(blockchain: Blockchain, w3=None, start_new: bool = False) -> dict:
    """Finalise in-flight sweeps, optionally look for new ones, then send what is ready.

    Must run under the chain's withdrawal lock: top-ups use the hot wallet nonce.
    """
    hot = HotWallet.objects.filter(blockchain=blockchain).first()
    has_open = Sweep.objects.filter(blockchain=blockchain, status__in=Sweep.OPEN_STATUSES).exists()
    if hot is None:
        if has_open or start_new:
            logger.warning('%s: no hot wallet, sweeping skipped', blockchain)
        return {'skipped': 'no hot wallet'}
    if not has_open and not start_new:
        return {}
    w3 = _get_w3(blockchain, w3)
    result = check_sweeps(blockchain, w3, hot)
    if start_new:
        result['created'] = create_sweeps(blockchain, w3, hot)
    result.update(send_pending(blockchain, w3, hot))
    return result
