from datetime import timedelta

import rlp
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from eth_account import Account
from eth_account._utils.legacy_transactions import Transaction
from eth_utils import keccak
from web3 import Web3
from web3.exceptions import TransactionNotFound

from . import sweeps as sw
from . import tasks
from .models import Blockchain, Coin, CoinInfo, CryptoAddress, HotWallet, Sweep, TransactionLog
from .reconciliation import expected_balances
from .scanner import TRANSFER_TOPIC
from .wallet import generate_keypair, get_or_create_deposit_address

User = get_user_model()
TEST_KEY = 'q2zQm1p2kqgk9b6r3XyUoJmYyq1x0n8nqQ3sV5hY6uE='
TOKEN = '0x' + 'aa' * 20
COLD = '0x' + 'cc' * 20
ETH = 10 ** 18
GAS_PRICE = 10


def topic(address):
    return '0x' + '0' * 24 + address.lower()[2:]


class FakeEth:
    """In-memory node: balances, token balances, and every signed transaction decoded."""

    def __init__(self, latest=1000, gas_price=GAS_PRICE, gas_estimate=21000):
        self.latest = latest
        self.gas_price = gas_price
        self.gas_estimate = gas_estimate
        self.estimate_error = None
        self.balances = {}  # lowercase address -> wei
        self.token_balances = {}  # (contract, address) lowercase -> amount
        self.sent = []  # decoded transactions
        self.receipts = {}
        self.nonces = {}  # lowercase address -> mined nonce count

    def get_block_number(self):
        return self.latest

    def get_balance(self, address):
        return self.balances.get(address.lower(), 0)

    def call(self, tx):
        assert tx['data'].startswith('0x70a08231')
        owner = '0x' + tx['data'][-40:]
        return self.token_balances.get((tx['to'].lower(), owner.lower()), 0).to_bytes(32, 'big')

    def estimate_gas(self, tx):
        if self.estimate_error:
            raise self.estimate_error
        return self.gas_estimate

    def get_transaction_count(self, address, block_identifier='latest'):
        sender = address.lower()
        if block_identifier == 'pending':
            return max([t['nonce'] + 1 for t in self.sent if t['from'] == sender], default=self.nonces.get(sender, 0))
        return self.nonces.get(sender, 0)

    def send_raw_transaction(self, raw):
        tx = rlp.decode(bytes(raw), Transaction)
        self.sent.append({
            'hash': '0x' + keccak(bytes(raw)).hex(), 'from': Account.recover_transaction(raw).lower(),
            'nonce': tx.nonce, 'gasPrice': tx.gasPrice, 'gas': tx.gas, 'to': '0x' + tx.to.hex(),
            'value': tx.value, 'data': tx.data.hex(),
        })

    def get_transaction(self, h):
        for t in self.sent:
            if t['hash'] == h:
                return t
        raise TransactionNotFound(h)

    def get_transaction_receipt(self, h):
        if h not in self.receipts:
            raise TransactionNotFound(h)
        return self.receipts[h]

    def mine(self, tx, status=1, block=900, gas_used=21000, logs=None):
        self.receipts[tx['hash']] = {'status': status, 'blockNumber': block, 'gasUsed': gas_used,
                                     'effectiveGasPrice': tx['gasPrice'], 'logs': logs or []}
        self.nonces[tx['from']] = max(self.nonces.get(tx['from'], 0), tx['nonce'] + 1)


class FakeWeb3:
    def __init__(self, eth):
        self.eth = eth


@override_settings(WALLET_ENCRYPTION_KEYS=[TEST_KEY])
class SweepTestBase(TestCase):
    def setUp(self):
        self.eth_coin = Coin.objects.create(symbol='ETH', name='Ethereum')
        self.usdt_coin = Coin.objects.create(symbol='USDT', name='Tether')
        self.chain = Blockchain.objects.create(name='Ethereum', chain_id=1, coin=self.eth_coin, confirm_times=3,
                                               is_auto_sync=True)
        self.native = CoinInfo.objects.create(blockchain=self.chain, coin=self.eth_coin, decimal=18,
                                              sweep_threshold=ETH // 10)
        self.usdt = CoinInfo.objects.create(blockchain=self.chain, coin=self.usdt_coin, decimal=6, contract=TOKEN,
                                            sweep_threshold=100)
        self.user = User.objects.create_user(email='s@test.com', password='pw', is_verified=True)
        self.address = get_or_create_deposit_address(self.user)
        self.addr = self.address.address.lower()
        self.hot = HotWallet(blockchain=self.chain)
        self.hot.address, key = generate_keypair()
        self.hot.set_private_key(key)
        self.hot.save()
        self.eth = FakeEth()
        self.w3 = FakeWeb3(self.eth)

    def deposit(self, coin_info, amount, n=1):
        TransactionLog.objects.create(
            tx_id='0x' + f'{n:064x}', log_index=-1 if not coin_info.contract else 0, blockchain=self.chain,
            coin_info=coin_info, user=self.user, direction=TransactionLog.Direction.DEPOSIT,
            status=TransactionLog.Status.CONFIRMED, recipient_address=self.addr, amount=amount, block_number=10)
        if coin_info.contract:
            self.eth.token_balances[(TOKEN, self.addr)] = amount
        else:
            self.eth.balances[self.addr] = self.eth.balances.get(self.addr, 0) + amount

    def sweep(self, start_new=True):
        return sw.process_chain_sweeps(self.chain, self.w3, start_new=start_new)

    def transfer_log(self, frm, to, amount):
        return {'address': TOKEN, 'topics': [TRANSFER_TOPIC, topic(frm), topic(to)], 'data': hex(amount)}


class NativeSweepTest(SweepTestBase):
    def test_native_balance_swept_to_hot_wallet_minus_fee(self):
        self.deposit(self.native, ETH)
        result = self.sweep()
        self.assertEqual((result['created'], result['sent']), (1, 1))

        tx = self.eth.sent[0]
        fee = 25200 * GAS_PRICE  # 21000 estimate + 20%
        self.assertEqual(tx['from'], self.addr)  # signed with the deposit address key
        self.assertEqual((tx['to'], tx['value'], tx['gas'], tx['nonce']), (self.hot.address.lower(), ETH - fee, 25200, 0))
        s = Sweep.objects.get()
        self.assertEqual((s.status, int(s.amount)), (Sweep.Status.BROADCAST, ETH - fee))

        # Mined and final: confirmed, and the log records what moved and the gas paid.
        self.eth.mine(tx, gas_used=21000)
        self.eth.balances[self.addr] = 4200 * GAS_PRICE  # unused gas stays on the address
        self.assertEqual(self.sweep(start_new=False)['confirmed'], 1)
        s.refresh_from_db()
        self.assertEqual(s.status, Sweep.Status.CONFIRMED)
        log = s.transaction_log
        self.assertEqual((log.direction, log.status, int(log.amount), int(log.fee)),
                         (TransactionLog.Direction.SWEEP, TransactionLog.Status.CONFIRMED, ETH - fee, 21000 * GAS_PRICE))
        # Reconciliation's expected balance matches the chain exactly.
        self.assertEqual(expected_balances(self.chain, self.native)[self.addr], 4200 * GAS_PRICE)

    def test_below_threshold_is_not_swept(self):
        self.deposit(self.native, ETH // 20)
        self.assertEqual(self.sweep()['created'], 0)
        self.assertEqual(self.eth.sent, [])

    def test_cancelled_when_fee_would_leave_less_than_threshold(self):
        self.deposit(self.native, ETH // 10)
        result = self.sweep()
        self.assertEqual((result['created'], result['cancelled']), (1, 1))
        self.assertEqual(Sweep.objects.get().status, Sweep.Status.CANCELLED)
        self.assertEqual(self.eth.sent, [])

    def test_sweeps_go_to_cold_wallet_when_hot_wallet_is_full(self):
        self.chain.cold_wallet_address = COLD
        self.chain.save()
        self.native.hot_wallet_limit = 5 * ETH
        self.native.save()
        self.eth.balances[self.hot.address.lower()] = 5 * ETH
        self.deposit(self.native, ETH)
        self.sweep()
        self.assertEqual(self.eth.sent[0]['to'], COLD)

    def test_not_sweeping_coins_without_threshold(self):
        CoinInfo.objects.filter(pk=self.native.pk).update(sweep_threshold=0)
        self.deposit(self.native, 10 * ETH)
        self.assertEqual(self.sweep()['created'], 0)

    def test_stuck_sweep_rebroadcast_with_same_nonce_and_original_can_still_confirm(self):
        self.deposit(self.native, ETH)
        self.sweep()
        first = self.eth.sent[0]
        Sweep.objects.update(broadcast_at=timezone.now() - timedelta(hours=1))
        self.eth.gas_price = 20
        self.assertEqual(self.sweep(start_new=False)['rebroadcast'], 1)

        second = self.eth.sent[1]
        self.assertEqual(second['nonce'], first['nonce'])
        self.assertEqual(second['gasPrice'], 20)
        self.assertEqual(second['value'], ETH - 25200 * 20)  # fee recomputed at the new price
        s = Sweep.objects.get()
        self.assertEqual(s.previous_tx_ids, [first['hash']])

        # The original one is mined after all: its hash and actual value are recorded.
        self.eth.mine(first)
        self.sweep(start_new=False)
        s.refresh_from_db()
        self.assertEqual((s.status, s.tx_id, int(s.amount)), (Sweep.Status.CONFIRMED, first['hash'], first['value']))
        self.assertEqual(int(s.transaction_log.amount), first['value'])

    def test_reverted_sweep_is_failed_and_fee_still_recorded(self):
        self.deposit(self.native, ETH)
        self.sweep()
        tx = self.eth.sent[0]
        self.eth.mine(tx, status=0)
        self.assertEqual(self.sweep(start_new=False)['failed'], 1)
        s = Sweep.objects.get()
        self.assertEqual((s.status, s.transaction_log.status, int(s.transaction_log.fee)),
                         (Sweep.Status.FAILED, TransactionLog.Status.FAILED, 21000 * GAS_PRICE))
        self.assertEqual(expected_balances(self.chain, self.native)[self.addr], ETH - 21000 * GAS_PRICE)

    def test_gas_estimate_failure_keeps_sweep_pending(self):
        self.deposit(self.native, ETH)
        self.eth.estimate_error = ValueError('node unavailable')
        self.sweep()
        s = Sweep.objects.get()
        self.assertEqual(s.status, Sweep.Status.PENDING)
        self.assertIn('node unavailable', s.error_message)
        self.eth.estimate_error = None
        self.assertEqual(self.sweep(start_new=False)['sent'], 1)

    def test_confirmation_waits_for_safe_block(self):
        self.deposit(self.native, ETH)
        self.sweep()
        self.eth.mine(self.eth.sent[0], block=999)  # latest is 1000, confirm_times 3
        self.assertEqual(self.sweep(start_new=False)['confirmed'], 0)
        self.eth.latest = 1002
        self.assertEqual(self.sweep(start_new=False)['confirmed'], 1)


class TokenSweepTest(SweepTestBase):
    def test_gas_top_up_then_token_sweep(self):
        self.deposit(self.usdt, 1_000)
        self.eth.gas_estimate = 50_000  # token transfer: 60_000 with margin, below the 100_000 minimum
        result = self.sweep()
        self.assertEqual((result['created'], result['topups']), (1, 1))

        topup = self.eth.sent[0]
        needed = 100_000 * GAS_PRICE * sw.TOPUP_GAS_PRICE_BUFFER
        self.assertEqual((topup['from'], topup['to'], topup['value']), (self.hot.address.lower(), self.addr, needed))
        self.hot.refresh_from_db()
        self.assertEqual(self.hot.next_nonce, 1)  # top-ups share the hot wallet nonce with withdrawals
        s = Sweep.objects.get()
        self.assertEqual((s.status, s.topup_count), (Sweep.Status.TOPPING_UP, 1))
        self.assertEqual((s.topup_log.direction, s.topup_log.status),
                         (TransactionLog.Direction.GAS_TOPUP, TransactionLog.Status.PENDING))

        # Top-up mined: the sweep itself is signed in the same run.
        self.eth.mine(topup)
        self.eth.balances[self.addr] = needed
        result = self.sweep(start_new=False)
        self.assertEqual((result['topped_up'], result['sent']), (1, 1))
        sweep_tx = self.eth.sent[1]
        self.assertEqual((sweep_tx['from'], sweep_tx['to'], sweep_tx['value']), (self.addr, TOKEN, 0))
        self.assertEqual(sweep_tx['data'], 'a9059cbb' + '0' * 24 + self.hot.address.lower()[2:] + f'{1_000:064x}')

        self.eth.mine(sweep_tx, gas_used=50_000, logs=[self.transfer_log(self.addr, self.hot.address, 1_000)])
        self.assertEqual(self.sweep(start_new=False)['confirmed'], 1)
        s.refresh_from_db()
        self.assertEqual(s.status, Sweep.Status.CONFIRMED)
        self.assertEqual(expected_balances(self.chain, self.usdt).get(self.addr), 0)
        # Native: top-up received minus the gas the sweep paid.
        self.assertEqual(expected_balances(self.chain, self.native)[self.addr], needed - 50_000 * GAS_PRICE)

    def test_no_top_up_when_address_already_has_gas(self):
        self.deposit(self.usdt, 1_000)
        self.eth.balances[self.addr] = 10 ** 9
        result = self.sweep()
        self.assertEqual((result['topups'], result['sent']), (0, 1))
        self.assertEqual(self.eth.sent[0]['from'], self.addr)

    def test_token_sweep_without_transfer_log_fails(self):
        self.deposit(self.usdt, 1_000)
        self.eth.balances[self.addr] = 10 ** 9
        self.sweep()
        self.eth.mine(self.eth.sent[0])  # status 1 but no Transfer event (token returned false)
        self.assertEqual(self.sweep(start_new=False)['failed'], 1)
        self.assertIn('Transfer event', Sweep.objects.get().error_message)

    def test_native_not_swept_while_token_sweep_is_open(self):
        self.deposit(self.usdt, 1_000, n=1)
        self.deposit(self.native, ETH, n=2)
        self.assertEqual(self.sweep()['created'], 1)
        self.assertEqual(Sweep.objects.get().coin_info, self.usdt)
        self.assertEqual(self.sweep()['created'], 0)  # one open sweep per address
        self.assertEqual(Sweep.objects.count(), 1)

    def test_stuck_top_up_rebroadcast_with_same_hot_wallet_nonce(self):
        self.deposit(self.usdt, 1_000)
        self.sweep()
        Sweep.objects.update(topup_broadcast_at=timezone.now() - timedelta(hours=1))
        self.eth.gas_price = 30
        self.assertEqual(self.sweep(start_new=False)['rebroadcast'], 1)
        first, second = self.eth.sent
        self.assertEqual((second['from'], second['nonce'], second['value']), (first['from'], first['nonce'], first['value']))
        self.assertEqual(second['gasPrice'], 30)
        self.hot.refresh_from_db()
        self.assertEqual(self.hot.next_nonce, 1)


class ScannerIgnoresPlatformWalletsTest(SweepTestBase):
    def test_gas_top_up_is_not_credited_as_a_deposit(self):
        from .scanner import ChainScanner
        from .tests import FakeEth as ScannerEth, FakeWeb3 as ScannerWeb3

        top_up = {'hash': '0x' + '77' * 32, 'from': self.hot.address, 'to': self.address.address, 'value': 5 * 10 ** 15}
        real = {'hash': '0x' + '88' * 32, 'from': '0x' + '33' * 20, 'to': self.address.address, 'value': 10 ** 16}
        eth = ScannerEth(latest=200, blocks={150: [top_up, real]},
                         receipts={top_up['hash']: {'status': 1}, real['hash']: {'status': 1}})
        Blockchain.objects.filter(pk=self.chain.pk).update(last_block=149)
        self.chain.refresh_from_db()
        self.chain.scan_batch_size = 10
        self.assertEqual(ChainScanner(self.chain, w3=ScannerWeb3(eth)).run(), 1)
        self.assertEqual(list(TransactionLog.objects.filter(direction='deposit').values_list('tx_id', flat=True)), [real['hash']])


class SweepTaskTest(SweepTestBase):
    @override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
    def test_dispatch_includes_chains_with_open_sweeps(self):
        from unittest import mock
        Sweep.objects.create(blockchain=self.chain, coin_info=self.native, crypto_address=self.address,
                             to_address=self.hot.address)
        with mock.patch.object(tasks.scan_chain, 'delay'), mock.patch.object(tasks.process_withdrawals, 'delay') as p:
            tasks.dispatch_chain_jobs()
        p.assert_called_once_with(self.chain.pk)

    @override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
    def test_sweep_deposits_task_starts_new_sweeps(self):
        from unittest import mock
        self.deposit(self.native, ETH)
        with mock.patch('blockchain.models.Blockchain.get_web3', return_value=self.w3):
            result = tasks.sweep_deposits()
        self.assertEqual((result[self.chain.pk]['created'], result[self.chain.pk]['sent']), (1, 1))
