"""Periodic health checks that raise operator alerts (see alerts.py).

Run every 5 minutes by Celery Beat (``blockchain.run_monitoring_checks``):

* Withdrawals approved more than ``ALERT_STUCK_MINUTES`` ago and still not confirmed
  (waiting to broadcast, broadcast or stuck), and withdrawals that failed.
* Sweeps open longer than ``ALERT_STUCK_MINUTES`` (twice that once a gas top-up was
  needed), and sweeps that failed or were cancelled after repeated send errors.
* Per chain: RPC unreachable; scanner more than ``ALERT_SCAN_LAG_BLOCKS`` behind the
  confirmed head; the hot wallet holding less of a coin than its
  ``hot_wallet_alert_threshold`` or than the withdrawals waiting for it.

Every check only reads (database and chain), so none needs the per-chain lock. Ages are
measured from approval / creation, not from the last broadcast: a rebroadcast resets
``broadcast_at``, so a transaction that keeps getting rebroadcast would never look old.

Also here: the reconciliation alert, and the Celery ``task_failure`` hook.
"""
import logging
from datetime import timedelta
from typing import Callable, Iterable, List

from celery.signals import task_failure
from django.conf import settings
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from django_central_wallet.utils.global_method import get_blockchain_real_amount

from .alerts import claim, send_alert
from .models import Blockchain, CoinInfo, HotWallet, Sweep, Withdrawal

logger = logging.getLogger(__name__)

FAILURE_LOOKBACK = timedelta(days=3)  # failures older than this are not reported (e.g. on first deploy)
FAILURE_KEY_SECONDS = int((FAILURE_LOOKBACK + timedelta(days=1)).total_seconds())  # report each failure once
MAX_LISTED = 20  # items listed in one alert
WITHDRAWAL_UNCONFIRMED = (Withdrawal.Status.APPROVED, Withdrawal.Status.BROADCAST, Withdrawal.Status.STUCK)
WITHDRAWAL_WAITING = (Withdrawal.Status.PENDING_REVIEW, Withdrawal.Status.APPROVED)  # not yet paid by the hot wallet


def _amount(coin_info, raw) -> str:
    return f'{get_blockchain_real_amount(coin_info, int(raw or 0))} {coin_info.coin.symbol if coin_info.coin else ""}'.strip()


def _listing(lines: List[str]) -> str:
    extra = len(lines) - MAX_LISTED
    return '\n'.join(lines[:MAX_LISTED] + ([f'... and {extra} more'] if extra > 0 else []))


def _alert_new_items(items: Iterable, key: Callable, line: Callable, subject: str, intro: str,
                     key_seconds=None) -> int:
    """One alert listing the items whose per-item key was not claimed recently."""
    lines = [line(item) for item in items if claim(key(item), key_seconds)]
    if not lines:
        return 0
    send_alert(None, subject.format(n=len(lines)), f'{intro}\n{_listing(lines)}')
    return len(lines)


# --------------------------------------------------------------------------- withdrawals / sweeps
def check_withdrawals() -> dict:
    now = timezone.now()
    result = {'unconfirmed': 0, 'failed': 0}
    minutes = settings.ALERT_STUCK_MINUTES
    if minutes > 0:
        qs = Withdrawal.objects.filter(status__in=WITHDRAWAL_UNCONFIRMED) \
            .annotate(approved_at=Coalesce('reviewed_at', 'created_at')) \
            .filter(approved_at__lt=now - timedelta(minutes=minutes)) \
            .select_related('coin_info__coin', 'coin_info__blockchain').order_by('id')
        result['unconfirmed'] = _alert_new_items(
            qs, key=lambda w: f'withdrawal-unconfirmed:{w.pk}',
            line=lambda w: f'#{w.pk} {w.coin_info.blockchain} {_amount(w.coin_info, w.amount)} -> {w.to_address}: '
                           f'{w.status}, approved {(now - w.approved_at).total_seconds() // 60:.0f} min ago'
                           f'{" (" + w.error_message[:200] + ")" if w.error_message else ""}',
            subject='{n} withdrawal(s) not confirmed in time',
            intro=f'Approved more than {minutes} minutes ago and still not confirmed:')
    qs = Withdrawal.objects.filter(status=Withdrawal.Status.FAILED, updated_at__gte=now - FAILURE_LOOKBACK) \
        .select_related('coin_info__coin', 'coin_info__blockchain').order_by('id')
    result['failed'] = _alert_new_items(
        qs, key=lambda w: f'withdrawal-failed:{w.pk}', key_seconds=FAILURE_KEY_SECONDS,
        line=lambda w: f'#{w.pk} {w.coin_info.blockchain} {_amount(w.coin_info, w.amount)} -> {w.to_address}: '
                       f'{w.error_message[:200] or "failed"}{f" (tx {w.tx_id})" if w.tx_id else ""}',
        subject='{n} withdrawal(s) failed',
        intro='Failed on chain and refunded to the user:')
    return result


def check_sweeps() -> dict:
    from .sweeps import MAX_SEND_ATTEMPTS

    now = timezone.now()
    result = {'unconfirmed': 0, 'failed': 0}
    minutes = settings.ALERT_STUCK_MINUTES
    if minutes > 0:
        # A token sweep that needed a gas top-up waits for two transactions.
        qs = Sweep.objects.filter(status__in=Sweep.OPEN_STATUSES).filter(
            Q(topup_count=0, created_at__lt=now - timedelta(minutes=minutes))
            | Q(topup_count__gt=0, created_at__lt=now - timedelta(minutes=2 * minutes))
        ).select_related('coin_info__coin', 'blockchain', 'crypto_address').order_by('id')
        result['unconfirmed'] = _alert_new_items(
            qs, key=lambda s: f'sweep-unconfirmed:{s.pk}',
            line=lambda s: f'#{s.pk} {s.blockchain} {s.coin_info.coin.symbol} from {s.crypto_address.address}: '
                           f'{s.status}, created {(now - s.created_at).total_seconds() // 60:.0f} min ago'
                           f'{" (" + s.error_message[:200] + ")" if s.error_message else ""}',
            subject='{n} sweep(s) / gas top-up(s) not confirmed in time',
            intro=f'Open for more than {minutes} minutes:')
    qs = Sweep.objects.filter(updated_at__gte=now - FAILURE_LOOKBACK).filter(
        Q(status=Sweep.Status.FAILED) | Q(status=Sweep.Status.CANCELLED, send_attempts__gte=MAX_SEND_ATTEMPTS)
    ).select_related('coin_info__coin', 'blockchain', 'crypto_address').order_by('id')
    result['failed'] = _alert_new_items(
        qs, key=lambda s: f'sweep-failed:{s.pk}', key_seconds=FAILURE_KEY_SECONDS,
        line=lambda s: f'#{s.pk} {s.blockchain} {s.coin_info.coin.symbol} from {s.crypto_address.address}: '
                       f'{s.status}: {s.error_message[:200]}',
        subject='{n} sweep(s) / gas top-up(s) failed',
        intro='Failed or cancelled after repeated errors:')
    return result


# --------------------------------------------------------------------------- per chain (RPC)
def check_scanner(blockchain: Blockchain, w3) -> bool:
    """Alert if the scanner is more than ALERT_SCAN_LAG_BLOCKS behind the confirmed head."""
    from .scanner import ScanError, safe_block_number

    max_lag = settings.ALERT_SCAN_LAG_BLOCKS
    if max_lag <= 0 or blockchain.last_block <= 0:  # off, or the cursor is not initialised yet
        return False
    try:
        head = safe_block_number(blockchain, w3)
    except ScanError as e:
        return send_alert(f'rpc-unreachable:{blockchain.pk}', f'{blockchain}: cannot read the chain head',
                          f'{blockchain}: {e}')
    lag = head - blockchain.last_block
    if lag <= max_lag:
        return False
    return send_alert(f'scan-lag:{blockchain.pk}', f'{blockchain}: deposit scanner is {lag} blocks behind',
                      f'{blockchain}: scanned up to block {blockchain.last_block}, confirmed head is {head} '
                      f'({lag} blocks behind, alert threshold {max_lag}). Deposits are credited late. '
                      f'Check the worker and the RPC endpoint (scan_batch_size, eth_getLogs limits).')


def check_hot_wallet(blockchain: Blockchain, hot: HotWallet, w3) -> int:
    """Alert per coin whose hot wallet balance is below its threshold or the withdrawals waiting for it."""
    from .sweeps import coin_balance

    waiting = dict(Withdrawal.objects.filter(coin_info__blockchain=blockchain, status__in=WITHDRAWAL_WAITING)
                   .values('coin_info_id').annotate(total=Sum('amount')).values_list('coin_info_id', 'total'))
    sent = 0
    for coin_info in CoinInfo.objects.filter(blockchain=blockchain, coin__isnull=False).select_related('coin', 'blockchain'):
        threshold = int(coin_info.hot_wallet_alert_threshold or 0)
        needed = int(waiting.get(coin_info.pk) or 0)
        if threshold <= 0 and needed <= 0:
            continue
        try:
            balance = coin_balance(w3, coin_info, hot.address)
        except Exception as e:
            logger.warning('%s: hot wallet balance of %s unavailable: %s', blockchain, coin_info, e)
            continue
        problems = []
        if 0 < threshold and balance < threshold:
            problems.append(f'below the alert threshold of {_amount(coin_info, threshold)}')
        if balance < needed:
            problems.append(f'less than the {_amount(coin_info, needed)} of withdrawals pending review or approved')
        if problems:
            sent += send_alert(f'hot-wallet-low:{coin_info.pk}', f'{coin_info}: hot wallet balance is low',
                               f'Hot wallet {hot.address} holds {_amount(coin_info, balance)}, '
                               f'{" and ".join(problems)}. Fund it (or lower hot_wallet_limit so sweeps refill it).')
    return sent


def check_chain(blockchain: Blockchain) -> dict:
    hot = HotWallet.objects.filter(blockchain=blockchain).first()
    w3 = blockchain.get_web3(middleware_inject=True)
    if w3 is None:
        send_alert(f'rpc-unreachable:{blockchain.pk}', f'{blockchain}: RPC unreachable',
                   f'{blockchain}: none of the configured RPC endpoints answered. Deposits, withdrawals and '
                   f'sweeps on this chain are paused until one is reachable.')
        return {'rpc': 'unreachable'}
    result = {'rpc': 'ok'}
    if blockchain.is_auto_sync:
        result['scan_lag_alert'] = _safe(check_scanner, blockchain, w3)
    if hot is not None:
        result['hot_wallet_alerts'] = _safe(check_hot_wallet, blockchain, hot, w3)
    return result


# --------------------------------------------------------------------------- entry points
def _safe(check, *args):
    """Run one check; a failing check must not stop the others."""
    try:
        return check(*args)
    except Exception as e:
        logger.exception('Monitoring check %s failed: %s', getattr(check, '__name__', check), e)
        return f'error: {type(e).__name__}'


def run_checks() -> dict:
    result = {'withdrawals': _safe(check_withdrawals), 'sweeps': _safe(check_sweeps)}
    chains = Blockchain.objects.filter(Q(is_auto_sync=True) | Q(hot_wallet__isnull=False)).distinct().order_by('pk')
    for blockchain in chains:
        result[f'chain:{blockchain.pk}'] = _safe(check_chain, blockchain)
    return result


def alert_discrepancies(blockchain: Blockchain, discrepancies) -> bool:
    """Reconciliation found on-chain balances that differ from the transaction log."""
    if not discrepancies:
        return False
    lines = [f'{d.address} {d.symbol}: on-chain={d.on_chain} expected={d.expected} diff={d.difference}'
             for d in discrepancies]
    return send_alert(f'reconciliation:{blockchain.pk}',
                      f'{blockchain}: {len(lines)} balance mismatch(es) found by reconciliation',
                      'On-chain balance differs from the transaction log (smallest unit):\n' + _listing(lines))


@task_failure.connect(dispatch_uid='blockchain_alert_on_task_failure')
def alert_on_task_failure(sender=None, task_id=None, exception=None, **kwargs):
    """Unhandled exception in any Celery task (including soft time limits)."""
    name = getattr(sender, 'name', None) or str(sender)
    error = type(exception).__name__
    send_alert(f'task-failure:{name}:{error}', f'Celery task {name} failed: {error}',
               f'Task {name} [{task_id}] raised {error}: {exception}\nThe traceback is in the worker log.')
