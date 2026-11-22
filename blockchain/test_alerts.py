import json
import os
import tempfile
import time
import urllib.error
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.db import OperationalError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from . import alerts, monitoring, tasks
from .locks import single_run_lock
from .models import Blockchain, Coin, CoinInfo, HotWallet, Sweep, Withdrawal
from .reconciliation import Discrepancy
from .scanner import ScanError
from .wallet import generate_keypair, get_or_create_deposit_address

User = get_user_model()
TEST_KEY = 'q2zQm1p2kqgk9b6r3XyUoJmYyq1x0n8nqQ3sV5hY6uE='
TOKEN = '0x' + 'aa' * 20
ETH = 10 ** 18
LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
WEBHOOK = 'https://hooks.example.com/services/T000/B000/secret-token'
ALERTS = dict(CACHES=LOCMEM, ADMINS=[('ops', 'ops@test.com')], ALERT_WEBHOOK_URL='', ALERT_DEDUP_SECONDS=3600,
              ALERT_STUCK_MINUTES=30, ALERT_SCAN_LAG_BLOCKS=100)


class FakeEth:
    def __init__(self, latest=1000):
        self.latest = latest
        self.balances = {}  # lowercase address -> wei
        self.token_balances = {}  # (contract, owner) lowercase -> amount
        self.calls = 0

    def get_block_number(self):
        return self.latest

    def get_balance(self, address, block_identifier='latest'):
        self.calls += 1
        return self.balances.get(address.lower(), 0)

    def call(self, tx):
        self.calls += 1
        owner = '0x' + tx['data'][-40:]
        return self.token_balances.get((tx['to'].lower(), owner.lower()), 0).to_bytes(32, 'big')


class FakeWeb3:
    def __init__(self, eth):
        self.eth = eth


def subjects():
    return [m.subject for m in mail.outbox]


# --------------------------------------------------------------------------- alerts.py
@override_settings(**ALERTS)
class SendAlertTest(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_email_sent_to_admins(self):
        self.assertTrue(alerts.send_alert('k', 'Something broke', 'details'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['ops@test.com'])
        self.assertIn('Something broke', mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].body, 'details')

    def test_same_key_is_deduplicated_other_keys_are_not(self):
        self.assertTrue(alerts.send_alert('k', 'A', 'x'))
        self.assertFalse(alerts.send_alert('k', 'A again', 'x'))
        self.assertTrue(alerts.send_alert('other', 'B', 'x'))
        self.assertEqual(len(mail.outbox), 2)

    def test_key_can_alert_again_after_the_dedup_window(self):
        with override_settings(ALERT_DEDUP_SECONDS=1):
            alerts.send_alert('k', 'A', 'x')
            cache.delete(alerts.CACHE_PREFIX + 'k')  # i.e. the window expired
            self.assertTrue(alerts.send_alert('k', 'A', 'x'))
        self.assertEqual(len(mail.outbox), 2)

    def test_dedup_disabled_and_no_key(self):
        with override_settings(ALERT_DEDUP_SECONDS=0):
            alerts.send_alert('k', 'A', 'x')
            alerts.send_alert('k', 'A', 'x')
        alerts.send_alert(None, 'B', 'x')
        alerts.send_alert(None, 'B', 'x')
        self.assertEqual(len(mail.outbox), 4)

    def test_webhook_posts_slack_compatible_json(self):
        with override_settings(ADMINS=[], ALERT_WEBHOOK_URL=WEBHOOK), \
                mock.patch('urllib.request.urlopen') as urlopen:
            self.assertTrue(alerts.send_alert('k', 'Subject', 'Body'))
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, WEBHOOK)
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(json.loads(request.data), {'text': 'Subject\nBody'})
        self.assertEqual(urlopen.call_args.kwargs['timeout'], 10)
        self.assertEqual(mail.outbox, [])

    def test_webhook_failure_is_logged_without_the_url_and_email_still_sent(self):
        errors = [urllib.error.URLError('timed out'), ValueError(f'unknown url type: {WEBHOOK}'),
                  urllib.error.HTTPError(WEBHOOK, 500, 'Server Error', {}, None)]
        for error in errors:
            cache.clear()
            mail.outbox = []
            with self.subTest(error=type(error).__name__), override_settings(ALERT_WEBHOOK_URL=WEBHOOK), \
                    mock.patch('urllib.request.urlopen', side_effect=error), \
                    self.assertLogs('blockchain.alerts', 'ERROR') as logs:
                self.assertTrue(alerts.send_alert('k', 'Subject', 'Body'))
            self.assertEqual(len(mail.outbox), 1)
            self.assertTrue(any('Alert webhook failed' in line for line in logs.output))
            self.assertFalse(any('hooks.example.com' in line or 'secret-token' in line for line in logs.output))

    def test_email_failure_does_not_raise(self):
        with mock.patch('blockchain.alerts.mail_admins', side_effect=OSError('SMTP down')), \
                self.assertLogs('blockchain.alerts', 'ERROR') as logs:
            self.assertTrue(alerts.send_alert('k', 'Subject', 'Body'))
        self.assertTrue(any('Alert email failed' in line for line in logs.output))

    def test_unexpected_error_does_not_raise(self):
        with mock.patch('blockchain.alerts._deliver', side_effect=RuntimeError('boom')), \
                self.assertLogs('blockchain.alerts', 'ERROR'):
            self.assertFalse(alerts.send_alert('k', 'Subject', 'Body'))

    def test_cache_failure_still_sends(self):
        with mock.patch.object(cache, 'add', side_effect=ConnectionError('redis down')), \
                self.assertLogs('blockchain.alerts', 'WARNING'):
            self.assertTrue(alerts.send_alert('k', 'Subject', 'Body'))
        self.assertEqual(len(mail.outbox), 1)

    def test_urls_are_redacted(self):
        alerts.send_alert('k', 'RPC https://mainnet.example.io/v3/APIKEY failed', 'see wss://node/x?key=1 now')
        self.assertNotIn('APIKEY', mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].body, 'see <url> now')

    def test_no_channel_configured_only_logs(self):
        with override_settings(ADMINS=[]), self.assertLogs('blockchain.alerts', 'ERROR') as logs:
            self.assertTrue(alerts.send_alert('k', 'Subject', 'Body'))
        self.assertEqual(mail.outbox, [])
        self.assertIn('ALERT Subject: Body', logs.output[0])


# --------------------------------------------------------------------------- monitoring.py
@override_settings(WALLET_ENCRYPTION_KEYS=[TEST_KEY], **ALERTS)
class MonitoringTestBase(TestCase):
    def setUp(self):
        cache.clear()
        self.eth_coin = Coin.objects.create(symbol='ETH', name='Ethereum')
        self.usdt_coin = Coin.objects.create(symbol='USDT', name='Tether')
        self.chain = Blockchain.objects.create(name='Ethereum', chain_id=1, coin=self.eth_coin, confirm_times=10,
                                               is_auto_sync=True, last_block=950)
        self.native = CoinInfo.objects.create(blockchain=self.chain, coin=self.eth_coin, decimal=18)
        self.usdt = CoinInfo.objects.create(blockchain=self.chain, coin=self.usdt_coin, decimal=6, contract=TOKEN)
        self.user = User.objects.create_user(email='m@test.com', password='pw', is_verified=True)
        self.address = get_or_create_deposit_address(self.user)
        self.hot = HotWallet(blockchain=self.chain)
        self.hot.address, key = generate_keypair()
        self.hot.set_private_key(key)
        self.hot.save()
        self.eth = FakeEth()
        self.w3 = FakeWeb3(self.eth)

    def ago(self, minutes):
        return timezone.now() - timedelta(minutes=minutes)

    def withdrawal(self, status, coin_info=None, amount=ETH, age=0, **fields):
        w = Withdrawal.objects.create(user=self.user, crypto_address=self.address, coin_info=coin_info or self.native,
                                      to_address='0x' + '11' * 20, amount=amount, status=status, **fields)
        Withdrawal.objects.filter(pk=w.pk).update(created_at=self.ago(age), updated_at=self.ago(age))
        return w

    def sweep(self, status, age=0, **fields):
        s = Sweep.objects.create(blockchain=self.chain, coin_info=self.native, crypto_address=self.address,
                                 to_address=self.hot.address, status=status, **fields)
        Sweep.objects.filter(pk=s.pk).update(created_at=self.ago(age), updated_at=self.ago(age))
        return s


class WithdrawalAlertTest(MonitoringTestBase):
    def test_unconfirmed_after_threshold_alerts_once_per_window(self):
        S = Withdrawal.Status
        old = [self.withdrawal(S.APPROVED, age=31), self.withdrawal(S.BROADCAST, age=60, broadcast_at=self.ago(1)),
               self.withdrawal(S.STUCK, age=45)]
        self.assertEqual(monitoring.check_withdrawals(), {'unconfirmed': 3, 'failed': 0})
        self.assertEqual(subjects(), ['[Central Wallet] 3 withdrawal(s) not confirmed in time'])
        for w in old:
            self.assertIn(f'#{w.pk} ', mail.outbox[0].body)
        # Still stuck on the next run: not repeated within ALERT_DEDUP_SECONDS.
        self.assertEqual(monitoring.check_withdrawals(), {'unconfirmed': 0, 'failed': 0})
        self.assertEqual(len(mail.outbox), 1)

    def test_age_counts_from_approval_not_from_the_last_rebroadcast(self):
        w = self.withdrawal(Withdrawal.Status.BROADCAST, age=120, broadcast_at=self.ago(2), reviewed_at=self.ago(40))
        self.assertEqual(monitoring.check_withdrawals()['unconfirmed'], 1)
        self.assertIn(f'#{w.pk} ', mail.outbox[0].body)

    def test_recent_pending_or_finished_withdrawals_do_not_alert(self):
        S = Withdrawal.Status
        self.withdrawal(S.APPROVED, age=5)
        self.withdrawal(S.BROADCAST, age=120, reviewed_at=self.ago(10))  # approved recently
        self.withdrawal(S.PENDING_REVIEW, age=600)  # waiting for an admin, not for the chain
        self.withdrawal(S.CONFIRMED, age=600)
        self.withdrawal(S.REJECTED, age=600)
        self.assertEqual(monitoring.check_withdrawals(), {'unconfirmed': 0, 'failed': 0})
        self.assertEqual(mail.outbox, [])

    def test_stuck_threshold_zero_disables(self):
        self.withdrawal(Withdrawal.Status.APPROVED, age=600)
        with override_settings(ALERT_STUCK_MINUTES=0):
            self.assertEqual(monitoring.check_withdrawals()['unconfirmed'], 0)

    def test_failed_withdrawal_alerts_exactly_once(self):
        w = self.withdrawal(Withdrawal.Status.FAILED, error_message='Transaction reverted on chain')
        self.withdrawal(Withdrawal.Status.FAILED, age=5 * 24 * 60)  # before the lookback window
        self.assertEqual(monitoring.check_withdrawals()['failed'], 1)
        self.assertEqual(subjects(), ['[Central Wallet] 1 withdrawal(s) failed'])
        self.assertIn(f'#{w.pk} ', mail.outbox[0].body)
        self.assertIn('Transaction reverted on chain', mail.outbox[0].body)
        with override_settings(ALERT_DEDUP_SECONDS=1):  # failures use their own, longer key lifetime
            self.assertEqual(monitoring.check_withdrawals()['failed'], 0)
        self.assertEqual(len(mail.outbox), 1)


class SweepAlertTest(MonitoringTestBase):
    def test_open_sweep_after_threshold_alerts(self):
        s = self.sweep(Sweep.Status.BROADCAST, age=31)
        self.assertEqual(monitoring.check_sweeps(), {'unconfirmed': 1, 'failed': 0})
        self.assertIn('not confirmed in time', mail.outbox[0].subject)
        self.assertIn(f'#{s.pk} ', mail.outbox[0].body)

    def test_recent_sweep_does_not_alert_and_top_up_gets_twice_the_time(self):
        self.sweep(Sweep.Status.BROADCAST, age=10)
        self.assertEqual(monitoring.check_sweeps()['unconfirmed'], 0)
        Sweep.objects.all().delete()
        s = self.sweep(Sweep.Status.TOPPING_UP, age=45, topup_count=1)
        self.assertEqual(monitoring.check_sweeps()['unconfirmed'], 0)
        Sweep.objects.filter(pk=s.pk).update(created_at=self.ago(61))
        self.assertEqual(monitoring.check_sweeps()['unconfirmed'], 1)
        self.assertIn('topping_up', mail.outbox[0].body)

    def test_failed_and_repeatedly_cancelled_sweeps_alert_once(self):
        from .sweeps import MAX_SEND_ATTEMPTS
        failed = self.sweep(Sweep.Status.FAILED, error_message='Gas top-up reverted on chain')
        other = User.objects.create_user(email='m2@test.com', password='pw')
        address2 = get_or_create_deposit_address(other)
        cancelled = Sweep.objects.create(blockchain=self.chain, coin_info=self.native, crypto_address=address2,
                                         to_address=self.hot.address, status=Sweep.Status.CANCELLED,
                                         send_attempts=MAX_SEND_ATTEMPTS, error_message='Cancelled after 10 failed attempts')
        self.assertEqual(monitoring.check_sweeps(), {'unconfirmed': 0, 'failed': 2})
        self.assertIn(f'#{failed.pk} ', mail.outbox[0].body)
        self.assertIn(f'#{cancelled.pk} ', mail.outbox[0].body)
        self.assertEqual(monitoring.check_sweeps()['failed'], 0)

    def test_normal_cancel_does_not_alert(self):
        self.sweep(Sweep.Status.CANCELLED, send_attempts=0, error_message='Balance after gas is below the threshold')
        self.sweep(Sweep.Status.CONFIRMED, age=600)
        self.assertEqual(monitoring.check_sweeps(), {'unconfirmed': 0, 'failed': 0})
        self.assertEqual(mail.outbox, [])


class ChainAlertTest(MonitoringTestBase):
    def test_scanner_lag_alerts(self):
        self.eth.latest = 1200  # confirmed head 1190, cursor at 950: 240 behind
        self.assertTrue(monitoring.check_scanner(self.chain, self.w3))
        self.assertEqual(subjects(), ['[Central Wallet] Ethereum: deposit scanner is 240 blocks behind'])
        self.assertFalse(monitoring.check_scanner(self.chain, self.w3))  # deduplicated

    def test_scanner_within_threshold_or_uninitialised_does_not_alert(self):
        self.eth.latest = 1060  # 100 behind: at the threshold, not beyond
        self.assertFalse(monitoring.check_scanner(self.chain, self.w3))
        self.chain.last_block = 0
        self.eth.latest = 10 ** 6
        self.assertFalse(monitoring.check_scanner(self.chain, self.w3))
        self.chain.last_block = 950
        with override_settings(ALERT_SCAN_LAG_BLOCKS=0):
            self.assertFalse(monitoring.check_scanner(self.chain, self.w3))
        self.assertEqual(mail.outbox, [])

    def test_unreadable_head_alerts_rpc(self):
        with mock.patch('blockchain.scanner.safe_block_number', side_effect=ScanError('timeout')):
            self.assertTrue(monitoring.check_scanner(self.chain, self.w3))
        self.assertIn('cannot read the chain head', mail.outbox[0].subject)

    def test_rpc_unreachable_alerts(self):
        with mock.patch('blockchain.models.Blockchain.get_web3', return_value=None):
            self.assertEqual(monitoring.check_chain(self.chain), {'rpc': 'unreachable'})
            monitoring.check_chain(self.chain)
        self.assertEqual(subjects(), ['[Central Wallet] Ethereum: RPC unreachable'])

    def test_reachable_chain_runs_scanner_and_hot_wallet_checks(self):
        with mock.patch('blockchain.models.Blockchain.get_web3', return_value=self.w3):
            self.assertEqual(monitoring.check_chain(self.chain),
                             {'rpc': 'ok', 'scan_lag_alert': False, 'hot_wallet_alerts': 0})
        self.assertEqual(mail.outbox, [])

    def test_hot_wallet_below_threshold_alerts(self):
        CoinInfo.objects.filter(pk=self.usdt.pk).update(hot_wallet_alert_threshold=1_000_000_000)
        self.eth.token_balances[(TOKEN, self.hot.address.lower())] = 999_000_000
        self.assertEqual(monitoring.check_hot_wallet(self.chain, self.hot, self.w3), 1)
        self.assertEqual(subjects(), ['[Central Wallet] Ethereum - USDT: hot wallet balance is low'])
        self.assertIn('holds 999 USDT, below the alert threshold of 1000 USDT', mail.outbox[0].body)
        self.assertEqual(monitoring.check_hot_wallet(self.chain, self.hot, self.w3), 0)  # deduplicated

    def test_hot_wallet_above_threshold_does_not_alert(self):
        CoinInfo.objects.filter(pk=self.native.pk).update(hot_wallet_alert_threshold=ETH)
        self.eth.balances[self.hot.address.lower()] = ETH
        self.assertEqual(monitoring.check_hot_wallet(self.chain, self.hot, self.w3), 0)
        self.assertEqual(mail.outbox, [])

    def test_hot_wallet_cannot_cover_waiting_withdrawals(self):
        S = Withdrawal.Status
        self.withdrawal(S.PENDING_REVIEW, amount=2 * ETH)
        self.withdrawal(S.APPROVED, amount=ETH)
        self.withdrawal(S.CONFIRMED, amount=100 * ETH)  # already paid: not counted
        self.eth.balances[self.hot.address.lower()] = 2 * ETH
        self.assertEqual(monitoring.check_hot_wallet(self.chain, self.hot, self.w3), 1)
        self.assertIn('less than the 3 ETH of withdrawals pending review or approved', mail.outbox[0].body)

    def test_hot_wallet_covering_waiting_withdrawals_does_not_alert(self):
        self.withdrawal(Withdrawal.Status.APPROVED, amount=ETH)
        self.eth.balances[self.hot.address.lower()] = 3 * ETH
        self.assertEqual(monitoring.check_hot_wallet(self.chain, self.hot, self.w3), 0)

    def test_no_threshold_and_nothing_waiting_reads_nothing(self):
        self.assertEqual(monitoring.check_hot_wallet(self.chain, self.hot, self.w3), 0)
        self.assertEqual(self.eth.calls, 0)


class MonitoringTaskTest(MonitoringTestBase):
    def test_task_runs_all_checks_and_survives_a_failing_one(self):
        self.withdrawal(Withdrawal.Status.FAILED)
        with mock.patch('blockchain.monitoring.check_sweeps', side_effect=RuntimeError('db hiccup')), \
                mock.patch('blockchain.models.Blockchain.get_web3', return_value=None), \
                self.assertLogs('blockchain.monitoring', 'ERROR'):
            result = tasks.run_monitoring_checks()
        self.assertEqual(result['withdrawals'], {'unconfirmed': 0, 'failed': 1})
        self.assertEqual(result['sweeps'], 'error: RuntimeError')
        self.assertEqual(result[f'chain:{self.chain.pk}'], {'rpc': 'unreachable'})
        self.assertEqual(len(mail.outbox), 2)

    def test_task_skips_when_already_running(self):
        with single_run_lock('monitoring', 60):
            self.assertEqual(tasks.run_monitoring_checks(), 'skipped: already running')

    def test_task_is_scheduled(self):
        from django.conf import settings
        names = [entry['task'] for entry in settings.CELERY_BEAT_SCHEDULE.values()]
        self.assertIn('blockchain.run_monitoring_checks', names)

    def test_reconciliation_mismatch_alerts(self):
        found = [Discrepancy(self.address.address, self.native.pk, 'ETH', on_chain=5, expected=3)]
        with mock.patch('blockchain.reconciliation.find_discrepancies', return_value=found):
            self.assertEqual(tasks.reconcile_balances(), 1)
        self.assertEqual(subjects(), ['[Central Wallet] Ethereum: 1 balance mismatch(es) found by reconciliation'])
        self.assertIn(f'{self.address.address} ETH: on-chain=5 expected=3 diff=2', mail.outbox[0].body)

    def test_reconciliation_without_mismatch_does_not_alert(self):
        with mock.patch('blockchain.reconciliation.find_discrepancies', return_value=[]):
            self.assertEqual(tasks.reconcile_balances(), 0)
        self.assertEqual(mail.outbox, [])


@override_settings(**ALERTS)
class TaskFailureAlertTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_unhandled_task_exception_alerts(self):
        with mock.patch('blockchain.tasks.Blockchain.objects') as qs, self.assertLogs('blockchain.alerts', 'ERROR'):
            qs.filter.side_effect = RuntimeError('database is gone')
            result = tasks.dispatch_chain_jobs.apply()
        self.assertTrue(result.failed())
        self.assertEqual(subjects(), ['[Central Wallet] Celery task blockchain.dispatch_chain_jobs failed: RuntimeError'])
        self.assertIn('RuntimeError: database is gone', mail.outbox[0].body)

    def test_signal_alert_is_deduplicated_per_task_and_error(self):
        from celery.signals import task_failure
        for _ in range(2):
            task_failure.send(sender=tasks.scan_chain, task_id='1', exception=ValueError('x'))
        task_failure.send(sender=tasks.scan_chain, task_id='2', exception=KeyError('y'))
        self.assertEqual(len(mail.outbox), 2)

    def test_successful_task_does_not_alert(self):
        with mock.patch.object(tasks.scan_chain, 'delay'), mock.patch.object(tasks.process_withdrawals, 'delay'):
            self.assertTrue(tasks.dispatch_chain_jobs.apply().successful())
        self.assertEqual(mail.outbox, [])


# --------------------------------------------------------------------------- /health/ and beat heartbeat
@override_settings(CACHES=LOCMEM)
class HealthCheckTest(TestCase):
    def test_ok(self):
        with mock.patch('django_central_wallet.health.check_redis', return_value=True):
            response = self.client.get('/health/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok', 'checks': {'database': 'ok', 'redis': 'ok'}})
        self.assertIn('no-cache', response['Cache-Control'])

    def test_redis_down(self):
        # Not logged as a server error: with ADMINS set that would email on every probe.
        with mock.patch('redis.Redis.ping', side_effect=ConnectionError('refused')), \
                self.assertNoLogs('django.request'):
            response = self.client.get('/health/')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'status': 'failing', 'checks': {'database': 'ok', 'redis': 'failing'}})

    def test_database_down(self):
        with mock.patch('django_central_wallet.health.check_redis', return_value=True), \
                mock.patch('django_central_wallet.health.connection.cursor', side_effect=OperationalError('down')):
            response = self.client.get('/health/')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'status': 'failing', 'checks': {'database': 'failing', 'redis': 'ok'}})

    @override_settings(ALLOWED_HOSTS=['wallet.example.com'], SECURE_SSL_REDIRECT=True, DEBUG=False)
    def test_answers_probes_regardless_of_host_and_https_settings(self):
        with mock.patch('django_central_wallet.health.check_redis', return_value=True):
            response = self.client.get('/health/', HTTP_HOST='127.0.0.1:8000')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.client.head('/health/', HTTP_HOST='10.0.0.5').status_code, 200)
        # Other paths still get the normal host check and redirect.
        self.assertEqual(self.client.get('/admin/', HTTP_HOST='127.0.0.1:8000').status_code, 400)
        self.assertEqual(self.client.get('/admin/', HTTP_HOST='wallet.example.com').status_code, 301)

    def test_no_authentication_and_only_safe_methods(self):
        with mock.patch('django_central_wallet.health.check_redis', return_value=True):
            self.assertEqual(self.client.get('/health/').status_code, 200)
            self.assertEqual(self.client.post('/health/').status_code, 405)

    def test_redis_ping_uses_redis_url(self):
        from django_central_wallet import health
        with override_settings(REDIS_URL='redis://example:6379/3'), \
                mock.patch('redis.Redis.from_url') as from_url:
            from_url.return_value.ping.return_value = True
            self.assertTrue(health.check_redis())
        self.assertEqual(from_url.call_args.args[0], 'redis://example:6379/3')


class BeatHeartbeatTest(SimpleTestCase):
    def test_tick_writes_heartbeat(self):
        from django_central_wallet import beat
        path = os.path.join(tempfile.mkdtemp(), 'heartbeat')
        with mock.patch.object(beat, 'HEARTBEAT_FILE', path):
            self.assertFalse(beat.heartbeat_is_fresh())
            scheduler = beat.HeartbeatScheduler.__new__(beat.HeartbeatScheduler)
            with mock.patch('celery.beat.PersistentScheduler.tick', return_value=5) as tick:
                self.assertEqual(scheduler.tick(), 5)
            tick.assert_called_once()
            self.assertTrue(beat.heartbeat_is_fresh())
            old = time.time() - beat.MAX_AGE - 1
            os.utime(path, (old, old))
            self.assertFalse(beat.heartbeat_is_fresh())

    def test_heartbeat_write_error_does_not_break_scheduling(self):
        from django_central_wallet import beat
        with mock.patch.object(beat, 'HEARTBEAT_FILE', os.path.join(tempfile.mkdtemp(), 'missing', 'heartbeat')):
            scheduler = beat.HeartbeatScheduler.__new__(beat.HeartbeatScheduler)
            with mock.patch('celery.beat.PersistentScheduler.tick', return_value=1), \
                    self.assertLogs('django_central_wallet.beat', 'WARNING'):
                self.assertEqual(scheduler.tick(), 1)

    def test_configured_as_beat_scheduler(self):
        from django.conf import settings
        self.assertEqual(settings.CELERY_BEAT_SCHEDULER, 'django_central_wallet.beat:HeartbeatScheduler')
