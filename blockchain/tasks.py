"""Periodic jobs (run by Celery Beat, see CELERY_BEAT_SCHEDULE in settings)."""
import logging
import time

from celery import shared_task

from .locks import single_run_lock
from .models import Blockchain, CoinInfo, Sweep, Withdrawal

logger = logging.getLogger(__name__)

# Locks expire after this many seconds; tasks are time limited below it.
LOCK_TIMEOUT = 600
TASK_TIME_LIMIT = 540
SCAN_TIME_BUDGET = 45  # seconds a scan run may keep catching up before yielding


@shared_task(name='blockchain.dispatch_chain_jobs')
def dispatch_chain_jobs():
    """Fan out per-chain jobs: scanning for auto-sync chains; withdrawals and in-flight sweeps
    wherever any are open."""
    for pk in Blockchain.objects.filter(is_auto_sync=True).values_list('pk', flat=True):
        scan_chain.delay(pk)
    open_statuses = [Withdrawal.Status.APPROVED, Withdrawal.Status.BROADCAST, Withdrawal.Status.STUCK]
    chain_ids = set(Withdrawal.objects.filter(status__in=open_statuses)
                    .values_list('coin_info__blockchain_id', flat=True).distinct())
    chain_ids |= set(Sweep.objects.filter(status__in=Sweep.OPEN_STATUSES).values_list('blockchain_id', flat=True).distinct())
    for pk in sorted(chain_ids):
        process_withdrawals.delay(pk)


@shared_task(name='blockchain.scan_chain', soft_time_limit=TASK_TIME_LIMIT)
def scan_chain(blockchain_id: int):
    from .scanner import ChainScanner, ScanError

    with single_run_lock(f'scan:{blockchain_id}', LOCK_TIMEOUT) as acquired:
        if not acquired:
            return 'skipped: already running'
        blockchain = Blockchain.objects.get(pk=blockchain_id)
        scanner = ChainScanner(blockchain)
        credited = 0
        started = time.monotonic()
        try:
            # Keep going while full batches are processed (i.e. we are behind), within a time budget.
            while True:
                before = blockchain.last_block
                credited += scanner.run()
                if blockchain.last_block - before < blockchain.scan_batch_size:
                    break
                if time.monotonic() - started > SCAN_TIME_BUDGET:
                    break
        except ScanError as e:
            logger.warning('%s: scan aborted: %s', blockchain, e)
        return credited


@shared_task(name='blockchain.process_withdrawals', soft_time_limit=TASK_TIME_LIMIT)
def process_withdrawals(blockchain_id: int):
    from .withdrawals import BroadcastError, process_chain_withdrawals

    with single_run_lock(f'withdrawals:{blockchain_id}', LOCK_TIMEOUT) as acquired:
        if not acquired:
            return 'skipped: already running'
        blockchain = Blockchain.objects.get(pk=blockchain_id)
        try:
            # Confirm / finalise, rebroadcast stuck ones (same nonce), then send approved ones.
            result = process_chain_withdrawals(blockchain)
        except BroadcastError as e:
            logger.warning('%s: withdrawal processing aborted: %s', blockchain, e)
            result = str(e)
        # Advance in-flight sweeps under the same lock (gas top-ups use the hot wallet nonce).
        return {'withdrawals': result, 'sweeps': _run_sweeps(blockchain, start_new=False)}


def _run_sweeps(blockchain, start_new: bool):
    from .sweeps import process_chain_sweeps
    from .withdrawals import BroadcastError

    try:
        return process_chain_sweeps(blockchain, start_new=start_new)
    except BroadcastError as e:
        logger.warning('%s: sweeping aborted: %s', blockchain, e)
        return str(e)


@shared_task(name='blockchain.sweep_deposits', soft_time_limit=TASK_TIME_LIMIT)
def sweep_deposits():
    """Look for deposit addresses to sweep on every auto-sync chain that has a coin with a sweep threshold."""
    chain_ids = CoinInfo.objects.filter(sweep_threshold__gt=0, blockchain__is_auto_sync=True) \
        .values_list('blockchain_id', flat=True).distinct()
    results = {}
    for pk in sorted(set(chain_ids)):
        with single_run_lock(f'withdrawals:{pk}', LOCK_TIMEOUT) as acquired:
            if not acquired:
                results[pk] = 'skipped: already running'
                continue
            results[pk] = _run_sweeps(Blockchain.objects.get(pk=pk), start_new=True)
    return results


@shared_task(name='blockchain.reconcile_balances', soft_time_limit=3600)
def reconcile_balances():
    from .reconciliation import find_discrepancies
    from .scanner import ScanError

    total = 0
    for blockchain in Blockchain.objects.filter(is_auto_sync=True):
        try:
            total += len(find_discrepancies(blockchain))
        except ScanError as e:
            logger.warning('%s: reconciliation skipped: %s', blockchain, e)
    return total
