from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APITestCase

from .models import *
from .scanner import TRANSFER_TOPIC, ChainScanner, ScanError

User = get_user_model()

USER_ADDR = '0x' + '11' * 20
OTHER_ADDR = '0x' + '22' * 20
SENDER = '0x' + '33' * 20
TOKEN = '0x' + 'aa' * 20


def addr_topic(address):
    return '0x' + '0' * 24 + address[2:]


def tx_hash(n):
    return '0x' + f'{n:064x}'


class FakeEth:
    """Minimal stand-in for ``w3.eth`` backed by in-memory blocks / logs / receipts."""

    def __init__(self, latest=1000, blocks=None, logs=None, receipts=None, max_log_range=None):
        self.latest = latest
        self.blocks = blocks or {}
        self.logs = logs or []
        self.receipts = receipts or {}
        self.max_log_range = max_log_range
        self.get_logs_calls = []

    def get_block_number(self):
        return self.latest

    def get_block(self, number, full_transactions=False):
        if number == 'finalized':
            return {'number': self.latest - 2}
        return {'number': number, 'transactions': self.blocks.get(number, [])}

    def get_logs(self, params):
        start, end = params['fromBlock'], params['toBlock']
        self.get_logs_calls.append((start, end))
        if self.max_log_range is not None and end - start + 1 > self.max_log_range:
            raise ValueError('query returned more than 10000 results')
        wanted = {a.lower() for a in params['address']}
        return [l for l in self.logs
                if start <= l['blockNumber'] <= end and l['address'].lower() in wanted]

    def get_transaction_receipt(self, h):
        return self.receipts[h]


class FakeWeb3:
    def __init__(self, eth):
        self.eth = eth


def transfer_log(block, h, log_index, to, amount, contract=TOKEN, removed=False):
    return {
        'address': contract,
        'blockNumber': block,
        'transactionHash': h,
        'logIndex': log_index,
        'topics': [TRANSFER_TOPIC, addr_topic(SENDER), addr_topic(to)],
        'data': hex(amount),
        'removed': removed,
    }


class BlockchainTest(APITestCase):
    def setUp(self):
        self.coin = Coin.objects.create(symbol='ETH', name='Ethereum')
        self.blockchain = Blockchain.objects.create(name='Ethereum Sepolia', rpc_url='http://localhost:8545', chain_id=11155111, coin=self.coin, block_explorer_url='https://sepolia.etherscan.io')
        self.coininfo = CoinInfo.objects.create(blockchain=self.blockchain, coin=self.coin)

        self.coin_usdt = Coin.objects.create(symbol='USDT', name='Test USDT')
        self.coininfo_usdt = CoinInfo.objects.create(blockchain=self.blockchain, coin=self.coin_usdt, contract='0x8B55E5e2Ae4D676b366CfCd13c7201A3f056bE86')

    def test_support_contracts(self):
        self.assertEqual(self.blockchain.get_support_contracts(), ['0x8b55e5e2ae4d676b366cfcd13c7201a3f056be86'])
        self.assertEqual(self.blockchain.get_native_coin_info(), self.coininfo)

    def test_get_confirm_latest_block_number(self):
        w3 = FakeWeb3(FakeEth(latest=500))
        self.assertEqual(self.blockchain.get_confirm_latest_block_number(w3), 500 - self.blockchain.confirm_times)


class ChainScannerTest(TestCase):
    def setUp(self):
        self.eth_coin = Coin.objects.create(symbol='ETH', name='Ethereum')
        self.usdt_coin = Coin.objects.create(symbol='USDT', name='Tether')
        self.chain = Blockchain.objects.create(name='Ethereum', chain_id=1, coin=self.eth_coin,
                                               confirm_times=10, last_block=100, scan_batch_size=50)
        self.native = CoinInfo.objects.create(blockchain=self.chain, coin=self.eth_coin, decimal=18)
        self.usdt = CoinInfo.objects.create(blockchain=self.chain, coin=self.usdt_coin, decimal=6, contract=TOKEN)
        self.user = User.objects.create_user(email='u@test.com', password='pw')
        self.address = CryptoAddress.objects.create(user=self.user, address=USER_ADDR.upper().replace('0X', '0x'), private_key='x')

    def balance(self, coin_info):
        b = CoinInfoBalance.objects.filter(crypto_address=self.address, coin_info=coin_info).first()
        return int(b.external_amount) if b else 0

    def scanner(self, eth):
        return ChainScanner(self.chain, w3=FakeWeb3(eth))

    def test_erc20_deposit_credited_from_logs(self):
        eth = FakeEth(latest=200, logs=[
            transfer_log(120, tx_hash(1), 3, USER_ADDR, 5_000_000),
            transfer_log(121, tx_hash(2), 0, OTHER_ADDR, 7_000_000),  # not ours
        ])
        credited = self.scanner(eth).run()

        self.assertEqual(credited, 1)
        self.assertEqual(self.balance(self.usdt), 5_000_000)
        log = TransactionLog.objects.get()
        self.assertEqual(log.direction, TransactionLog.Direction.DEPOSIT)
        self.assertEqual(log.status, TransactionLog.Status.CONFIRMED)
        self.assertEqual(log.user, self.user)
        self.assertEqual((log.tx_id, log.log_index, log.block_number), (tx_hash(1), 3, 120))
        self.chain.refresh_from_db()
        self.assertEqual(self.chain.last_block, 150)  # 100 + batch size 50

    def test_removed_logs_and_nft_transfers_ignored(self):
        nft = transfer_log(120, tx_hash(3), 1, USER_ADDR, 1)
        nft['topics'] = nft['topics'] + ['0x' + '0' * 63 + '1']
        eth = FakeEth(latest=200, logs=[transfer_log(120, tx_hash(1), 0, USER_ADDR, 10, removed=True), nft])
        self.assertEqual(self.scanner(eth).run(), 0)
        self.assertEqual(TransactionLog.objects.count(), 0)

    def test_native_deposit_requires_successful_receipt(self):
        eth = FakeEth(
            latest=200,
            blocks={
                110: [{'hash': tx_hash(10), 'from': SENDER, 'to': USER_ADDR, 'value': 10 ** 18}],
                111: [{'hash': tx_hash(11), 'from': SENDER, 'to': USER_ADDR, 'value': 2 * 10 ** 18}],
                112: [{'hash': tx_hash(12), 'from': SENDER, 'to': OTHER_ADDR, 'value': 10 ** 18},
                      {'hash': tx_hash(13), 'from': SENDER, 'to': None, 'value': 0}],
            },
            receipts={tx_hash(10): {'status': 1}, tx_hash(11): {'status': 0}},
        )
        self.assertEqual(self.scanner(eth).run(), 1)
        self.assertEqual(self.balance(self.native), 10 ** 18)  # reverted tx 11 is not credited
        self.assertEqual(TransactionLog.objects.get().log_index, TransactionLog.NATIVE_LOG_INDEX)

    def test_scan_native_can_be_disabled(self):
        self.chain.scan_native = False
        eth = FakeEth(latest=200, blocks={110: [{'hash': tx_hash(10), 'from': SENDER, 'to': USER_ADDR, 'value': 1}]},
                      receipts={tx_hash(10): {'status': 1}})
        self.assertEqual(self.scanner(eth).run(), 0)

    def test_rescan_does_not_double_credit(self):
        eth = FakeEth(
            latest=200,
            logs=[transfer_log(120, tx_hash(1), 0, USER_ADDR, 100)],
            blocks={120: [{'hash': tx_hash(2), 'from': SENDER, 'to': USER_ADDR, 'value': 5}]},
            receipts={tx_hash(2): {'status': 1}},
        )
        scanner = self.scanner(eth)
        self.assertEqual(scanner.run(), 2)
        self.assertEqual(scanner.rescan(120), 0)
        self.chain.last_block = 100
        self.assertEqual(self.scanner(eth).run(), 0)
        self.assertEqual(self.balance(self.usdt), 100)
        self.assertEqual(self.balance(self.native), 5)
        self.assertEqual(TransactionLog.objects.count(), 2)

    def test_same_tx_multiple_transfer_logs_each_credited(self):
        eth = FakeEth(latest=200, logs=[
            transfer_log(120, tx_hash(1), 0, USER_ADDR, 100),
            transfer_log(120, tx_hash(1), 1, USER_ADDR, 50),
        ])
        self.assertEqual(self.scanner(eth).run(), 2)
        self.assertEqual(self.balance(self.usdt), 150)

    def test_rpc_failure_keeps_cursor(self):
        eth = FakeEth(latest=200, logs=[transfer_log(120, tx_hash(1), 0, USER_ADDR, 100)])
        eth.get_block = mock.Mock(side_effect=ConnectionError('boom'))
        with self.assertRaises(ScanError):
            self.scanner(eth).run()
        self.chain.refresh_from_db()
        self.assertEqual(self.chain.last_block, 100)
        self.assertEqual(TransactionLog.objects.count(), 0)

    def test_get_logs_range_is_split_when_rejected(self):
        self.chain.scan_native = False
        eth = FakeEth(latest=200, max_log_range=10, logs=[
            transfer_log(105, tx_hash(1), 0, USER_ADDR, 1),
            transfer_log(149, tx_hash(2), 0, USER_ADDR, 2),
        ])
        self.assertEqual(self.scanner(eth).run(), 2)
        self.assertEqual(eth.get_logs_calls[0], (101, 150))
        self.assertGreater(len(eth.get_logs_calls), 1)

    def test_only_confirmed_blocks_scanned(self):
        eth = FakeEth(latest=115, logs=[transfer_log(106, tx_hash(1), 0, USER_ADDR, 1)])
        self.assertEqual(self.scanner(eth).run(), 0)  # target = 115 - 10 = 105
        self.chain.refresh_from_db()
        self.assertEqual(self.chain.last_block, 105)

    def test_finalized_tag(self):
        self.chain.use_finalized_block = True
        eth = FakeEth(latest=130)
        self.scanner(eth).run()
        self.chain.refresh_from_db()
        self.assertEqual(self.chain.last_block, 128)

    def test_fresh_chain_starts_at_current_block(self):
        self.chain.last_block = 0
        eth = FakeEth(latest=5000, logs=[transfer_log(1, tx_hash(1), 0, USER_ADDR, 1)])
        self.assertEqual(self.scanner(eth).run(), 0)
        self.chain.refresh_from_db()
        self.assertEqual(self.chain.last_block, 4990)


class ReconciliationTest(TestCase):
    setUp = ChainScannerTest.setUp
    scanner = ChainScannerTest.scanner

    def test_reports_unrecorded_funds(self):
        from .reconciliation import find_discrepancies

        self.scanner(FakeEth(latest=200, logs=[transfer_log(120, tx_hash(1), 0, USER_ADDR, 100)])).run()

        eth = FakeEth()
        eth.get_balance = mock.Mock(return_value=7)  # native coin arrived without being recorded
        token = mock.Mock()
        token.functions.balanceOf.return_value.call.return_value = 100
        with mock.patch('django_central_wallet.utils.global_method.get_erc20_contract', return_value=token):
            found = find_discrepancies(self.chain, w3=FakeWeb3(eth))

        self.assertEqual(len(found), 1)
        self.assertEqual((found[0].symbol, found[0].on_chain, found[0].expected), ('ETH', 7, 0))


class BlockchainAdminActionTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email='admin@test.com', password='pw')
        self.client.force_login(self.admin)
        self.chain = Blockchain.objects.create(name='Ethereum', last_block=100)

    def test_rescan_block_form_and_apply(self):
        url = '/admin/blockchain/blockchain/'
        response = self.client.post(url, {'action': 'rescan_block', '_selected_action': [self.chain.pk]})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="block_number"')

        with mock.patch('blockchain.scanner.ChainScanner.rescan', return_value=1) as rescan:
            response = self.client.post(url, {'action': 'rescan_block', '_selected_action': [self.chain.pk],
                                              'block_number': 123, 'apply': 'Rescan'}, follow=True)
        rescan.assert_called_once_with(123)
        self.assertContains(response, 'rescanned block 123')
