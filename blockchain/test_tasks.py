from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings

from . import tasks
from .locks import single_run_lock
from .models import Blockchain

LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


@override_settings(CACHES=LOCMEM, CELERY_TASK_ALWAYS_EAGER=True)
class TaskTest(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.auto = Blockchain.objects.create(name='Ethereum', is_auto_sync=True, last_block=100, scan_batch_size=10)
        self.manual = Blockchain.objects.create(name='BSC', is_auto_sync=False)

    def test_dispatch_only_auto_sync_chains_and_chains_with_open_withdrawals(self):
        with mock.patch.object(tasks.scan_chain, 'delay') as scan, \
                mock.patch.object(tasks.process_withdrawals, 'delay') as process:
            tasks.dispatch_chain_jobs()
            scan.assert_called_once_with(self.auto.pk)
            process.assert_not_called()

            with mock.patch('blockchain.models.Withdrawal.objects') as qs:
                qs.filter.return_value.values_list.return_value.distinct.return_value = [self.manual.pk]
                tasks.dispatch_chain_jobs()
            process.assert_called_once_with(self.manual.pk)

    def test_scan_chain_catches_up_in_batches(self):
        calls = []

        def fake_run(scanner):
            calls.append(scanner.blockchain.last_block)
            if len(calls) < 3:
                scanner.blockchain.last_block += scanner.blockchain.scan_batch_size  # full batch, still behind
            else:
                scanner.blockchain.last_block += 2  # caught up
            return 1

        with mock.patch('blockchain.scanner.ChainScanner.run', fake_run):
            self.assertEqual(tasks.scan_chain(self.auto.pk), 3)
        self.assertEqual(calls, [100, 110, 120])

    def test_scan_chain_skips_when_locked(self):
        with single_run_lock(f'scan:{self.auto.pk}', 60) as acquired:
            self.assertTrue(acquired)
            with mock.patch('blockchain.scanner.ChainScanner.run') as run:
                self.assertEqual(tasks.scan_chain(self.auto.pk), 'skipped: already running')
                run.assert_not_called()
        # released afterwards
        with mock.patch('blockchain.scanner.ChainScanner.run', return_value=0):
            self.assertEqual(tasks.scan_chain(self.auto.pk), 0)

    def test_scan_error_is_logged_not_raised(self):
        from .scanner import ScanError
        with mock.patch('blockchain.scanner.ChainScanner.run', side_effect=ScanError('rpc down')):
            self.assertEqual(tasks.scan_chain(self.auto.pk), 0)

    def test_process_withdrawals_runs_full_pass(self):
        with mock.patch('blockchain.models.Blockchain.get_web3', return_value=object()), \
                mock.patch('blockchain.withdrawals.check_broadcast_withdrawals',
                           return_value={'confirmed': 1, 'failed': 0, 'stuck': 0, 'requeued': 0}) as c, \
                mock.patch('blockchain.withdrawals.rebroadcast_stuck', return_value=1) as r, \
                mock.patch('blockchain.withdrawals.broadcast_approved', return_value=2) as b:
            self.assertEqual(tasks.process_withdrawals(self.auto.pk), {
                'withdrawals': {'confirmed': 1, 'failed': 0, 'stuck': 0, 'requeued': 0, 'rebroadcast': 1, 'sent': 2},
                'sweeps': {'skipped': 'no hot wallet'},
            })
        c.assert_called_once()
        r.assert_called_once()
        b.assert_called_once()

    def test_process_withdrawals_command_respects_lock(self):
        from django.core.management.base import CommandError
        with single_run_lock(f'withdrawals:{self.auto.pk}', 60):
            with self.assertRaises(CommandError):
                call_command('process_withdrawals', chain=self.auto.pk, stdout=mock.Mock())

    def test_scan_chain_command(self):
        with mock.patch('blockchain.scanner.ChainScanner.rescan', return_value=1) as rescan:
            call_command('scan_chain', chain=self.auto.pk, rescan_block=55, stdout=mock.Mock())
        rescan.assert_called_once_with(55)
        with mock.patch('blockchain.scanner.ChainScanner.run', return_value=0) as run:
            call_command('scan_chain', stdout=mock.Mock())  # all auto-sync chains
        run.assert_called_once()
