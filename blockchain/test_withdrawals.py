from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase
from web3.exceptions import TransactionNotFound

from django_central_wallet.utils.custom_exception_handler import (
    AmountBelowMinimumException, DailyLimitExceededException, InsufficientBalanceException,
    InvalidAddressException, InvalidAmountException,
)

from . import withdrawals as wd
from .models import Blockchain, Coin, CoinInfo, CoinInfoBalance, CryptoAddress, HotWallet, TransactionLog, Withdrawal
from .wallet import get_or_create_deposit_address

User = get_user_model()
TEST_KEY = 'q2zQm1p2kqgk9b6r3XyUoJmYyq1x0n8nqQ3sV5hY6uE='
DEST = '0x' + '44' * 20
TOKEN = '0x' + 'aa' * 20
ETH = 10 ** 18


class FakeEth:
    def __init__(self, latest=1000, pending_nonce=0, gas_price=10, send_error=None, mined_nonce=0):
        self.latest = latest
        self.pending_nonce = pending_nonce
        self.mined_nonce = mined_nonce
        self.gas_price = gas_price
        self.send_error = send_error
        self.sent = []
        self.receipts = {}

    def get_block_number(self):
        return self.latest

    def get_transaction_count(self, address, block_identifier='latest'):
        return self.pending_nonce if block_identifier == 'pending' else self.mined_nonce

    def send_raw_transaction(self, raw):
        if self.send_error:
            raise self.send_error
        self.sent.append(raw)

    def get_transaction_receipt(self, h):
        if h not in self.receipts:
            raise TransactionNotFound(h)
        return self.receipts[h]


class FakeWeb3:
    def __init__(self, eth):
        self.eth = eth


@override_settings(WALLET_ENCRYPTION_KEYS=[TEST_KEY])
class WithdrawalTestBase(TestCase):
    def setUp(self):
        self.eth_coin = Coin.objects.create(symbol='ETH', name='Ethereum')
        self.usdt_coin = Coin.objects.create(symbol='USDT', name='Tether')
        self.chain = Blockchain.objects.create(name='Ethereum', chain_id=1, coin=self.eth_coin, confirm_times=3,
                                               block_explorer_url='https://etherscan.io')
        self.native = CoinInfo.objects.create(blockchain=self.chain, coin=self.eth_coin, decimal=18)
        self.usdt = CoinInfo.objects.create(blockchain=self.chain, coin=self.usdt_coin, decimal=6, contract=TOKEN)
        self.user = User.objects.create_user(email='w@test.com', password='pw', is_verified=True)
        self.address = get_or_create_deposit_address(self.user)
        self.set_balance(self.native, 5 * ETH)
        self.set_balance(self.usdt, 1_000_000_000)

        self.hot = HotWallet(blockchain=self.chain, address='0x' + '55' * 20)
        from .wallet import generate_keypair
        self.hot.address, key = generate_keypair()
        self.hot.set_private_key(key)
        self.hot.save()

    def set_balance(self, coin_info, amount):
        CoinInfoBalance.objects.filter(crypto_address=self.address, coin_info=coin_info).update(external_amount=amount)

    def balance(self, coin_info):
        return int(CoinInfoBalance.objects.get(crypto_address=self.address, coin_info=coin_info).amount)


class WithdrawalRequestTest(WithdrawalTestBase):
    def test_parse_amount(self):
        self.assertEqual(wd.parse_amount(self.usdt, '1.5'), 1_500_000)
        self.assertEqual(wd.parse_amount(self.native, '0.000000000000000001'), 1)
        for bad in ['0', '-1', 'abc', '1.0000001', 'NaN', 'Infinity']:
            with self.assertRaises(InvalidAmountException, msg=bad):
                wd.parse_amount(self.usdt, bad)

    def test_request_debits_balance_and_needs_review_by_default(self):
        w = wd.request_withdrawal(self.user, self.native, DEST, ETH)
        self.assertEqual(w.status, Withdrawal.Status.PENDING_REVIEW)
        self.assertEqual(self.balance(self.native), 4 * ETH)
        self.assertEqual(w.transaction_log.direction, TransactionLog.Direction.WITHDRAWAL)
        self.assertEqual(w.transaction_log.status, TransactionLog.Status.PENDING)

    def test_auto_approve_limit(self):
        self.native.auto_approve_withdrawal_limit = ETH
        self.native.save()
        self.assertEqual(wd.request_withdrawal(self.user, self.native, DEST, ETH + 1).status, Withdrawal.Status.PENDING_REVIEW)
        WithdrawalRequestTest.reject_all(self)
        self.assertEqual(wd.request_withdrawal(self.user, self.native, DEST, ETH).status, Withdrawal.Status.APPROVED)

    def test_auto_approve_limit_applies_to_daily_total(self):
        self.native.auto_approve_withdrawal_limit = ETH
        self.native.save()
        statuses = [wd.request_withdrawal(self.user, self.native, DEST, ETH // 2).status for _ in range(3)]
        self.assertEqual(statuses, [Withdrawal.Status.APPROVED, Withdrawal.Status.APPROVED, Withdrawal.Status.PENDING_REVIEW])

    def reject_all(self):
        for w in Withdrawal.objects.all():
            wd.reject(w)

    def test_debits_an_address_that_holds_enough(self):
        second = CryptoAddress(user=self.user, address='0x' + '66' * 20)
        second.set_private_key('0x1')
        second.save()
        from .wallet import ensure_balances
        ensure_balances(second)
        CoinInfoBalance.objects.filter(crypto_address=second, coin_info=self.native).update(external_amount=10 * ETH)
        w = wd.request_withdrawal(self.user, self.native, DEST, 7 * ETH)
        self.assertEqual(w.crypto_address, second)
        with self.assertRaises(InvalidAddressException):  # any own address is refused as destination
            wd.request_withdrawal(self.user, self.native, second.address, ETH)

    def test_validation(self):
        with self.assertRaises(InvalidAddressException):
            wd.request_withdrawal(self.user, self.native, '0x1234', ETH)
        with self.assertRaises(InvalidAddressException):  # bad checksum
            wd.request_withdrawal(self.user, self.native, '0xAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', ETH)
        with self.assertRaises(InvalidAddressException):  # own deposit address
            wd.request_withdrawal(self.user, self.native, self.address.address, ETH)
        with self.assertRaises(InsufficientBalanceException):
            wd.request_withdrawal(self.user, self.native, DEST, 6 * ETH)
        self.native.min_withdrawal_amount = ETH
        self.native.save()
        with self.assertRaises(AmountBelowMinimumException):
            wd.request_withdrawal(self.user, self.native, DEST, ETH - 1)
        self.assertEqual(self.balance(self.native), 5 * ETH)

    def test_daily_limit(self):
        self.native.daily_withdrawal_limit = 2 * ETH
        self.native.save()
        w = wd.request_withdrawal(self.user, self.native, DEST, ETH)
        wd.request_withdrawal(self.user, self.native, DEST, ETH)
        with self.assertRaises(DailyLimitExceededException):
            wd.request_withdrawal(self.user, self.native, DEST, 1)
        wd.reject(w)  # rejected ones don't count
        wd.request_withdrawal(self.user, self.native, DEST, 1)

    def test_reject_refunds(self):
        w = wd.request_withdrawal(self.user, self.native, DEST, ETH)
        self.assertTrue(wd.reject(w))
        self.assertFalse(wd.reject(w))  # only once
        self.assertEqual(self.balance(self.native), 5 * ETH)
        w.refresh_from_db()
        self.assertEqual(w.status, Withdrawal.Status.REJECTED)
        self.assertEqual(w.transaction_log.status, TransactionLog.Status.FAILED)


class WithdrawalBroadcastTest(WithdrawalTestBase):
    def approved(self, coin_info=None, amount=ETH):
        w = wd.request_withdrawal(self.user, coin_info or self.native, DEST, amount)
        wd.approve(w)
        return w

    def test_pending_review_is_not_broadcast(self):
        w = wd.request_withdrawal(self.user, self.native, DEST, ETH)
        eth = FakeEth()
        self.assertFalse(wd.broadcast(w, FakeWeb3(eth)))
        self.assertEqual(eth.sent, [])

    def test_native_broadcast_signs_correct_tx(self):
        from eth_account import Account
        from eth_account._utils.legacy_transactions import Transaction
        import rlp

        w = self.approved()
        eth = FakeEth(pending_nonce=7, gas_price=123)
        self.assertTrue(wd.broadcast(w, FakeWeb3(eth)))
        w.refresh_from_db()
        self.assertEqual((w.status, w.nonce, int(w.gas_price)), (Withdrawal.Status.BROADCAST, 7, 123))
        self.hot.refresh_from_db()
        self.assertEqual(self.hot.next_nonce, 8)

        raw = eth.sent[0]
        self.assertEqual(Account.recover_transaction(raw), self.hot.address)
        tx = rlp.decode(bytes(raw), Transaction)
        self.assertEqual((tx.nonce, tx.gasPrice, tx.value, tx.to.hex()), (7, 123, ETH, DEST[2:]))
        self.assertEqual(w.transaction_log.tx_id, w.tx_id)

    def test_erc20_broadcast_calls_transfer(self):
        from eth_account._utils.legacy_transactions import Transaction
        import rlp

        w = self.approved(self.usdt, 2_500_000)
        eth = FakeEth()
        wd.broadcast(w, FakeWeb3(eth))
        tx = rlp.decode(bytes(eth.sent[0]), Transaction)
        self.assertEqual(tx.to.hex(), TOKEN[2:])
        self.assertEqual(tx.value, 0)
        self.assertEqual(tx.gas, self.chain.gas_limit_erc20)
        self.assertEqual(tx.data.hex(), 'a9059cbb' + '0' * 24 + DEST[2:] + f'{2_500_000:064x}')

    def test_nonce_uses_local_counter_when_node_is_behind(self):
        self.hot.next_nonce = 10
        self.hot.save()
        w = self.approved()
        wd.broadcast(w, FakeWeb3(FakeEth(pending_nonce=3)))
        w.refresh_from_db()
        self.assertEqual(w.nonce, 10)

    def test_send_error_keeps_nonce_and_never_resigns_with_a_new_one(self):
        # A gateway can return an error after forwarding the tx: the withdrawal must keep its nonce.
        w = self.approved()
        eth = FakeEth(pending_nonce=4, send_error=ValueError({'code': -32000, 'message': 'upstream timeout'}))
        self.assertFalse(wd.broadcast(w, FakeWeb3(eth)))
        w.refresh_from_db()
        self.assertEqual((w.status, w.nonce), (Withdrawal.Status.BROADCAST, 4))
        self.assertIn('upstream timeout', w.error_message)
        self.assertFalse(wd.reject(w))  # may already be paid
        self.hot.refresh_from_db()
        self.assertEqual(self.hot.next_nonce, 5)

        # Not broadcast again with another nonce, even when the node's pending count moves on.
        eth.send_error, eth.pending_nonce = None, 5
        self.assertEqual(wd.broadcast_approved(self.chain, FakeWeb3(eth)), 0)
        # Once stuck it is re-sent with the same nonce.
        Withdrawal.objects.filter(pk=w.pk).update(status=Withdrawal.Status.STUCK)
        self.assertEqual(wd.rebroadcast_stuck(self.chain, FakeWeb3(eth)), 1)
        w.refresh_from_db()
        self.assertEqual((w.status, w.nonce), (Withdrawal.Status.BROADCAST, 4))
        self.assertEqual(self.balance(self.native), 4 * ETH)

    def test_requeue_only_when_nonce_provably_taken_by_another_tx(self):
        w = self.approved()
        eth = FakeEth(latest=100, pending_nonce=2)
        wd.broadcast(w, FakeWeb3(eth))
        w.refresh_from_db()
        first_tx = w.tx_id

        eth.mined_nonce = 3  # nonce 2 used in a final block, but no receipt for our hash
        self.assertEqual(wd.check_broadcast_withdrawals(self.chain, FakeWeb3(eth))['requeued'], 0)  # not stuck yet
        Withdrawal.objects.filter(pk=w.pk).update(status=Withdrawal.Status.STUCK)
        self.assertEqual(wd.check_broadcast_withdrawals(self.chain, FakeWeb3(eth))['requeued'], 1)
        w.refresh_from_db()
        self.assertEqual((w.status, w.nonce, w.tx_id), (Withdrawal.Status.APPROVED, None, None))
        self.assertEqual(w.previous_tx_ids, [first_tx])
        self.assertFalse(wd.reject(w))  # it was signed before: never refund by hand

        eth.pending_nonce = 3
        wd.broadcast(w, FakeWeb3(eth))
        w.refresh_from_db()
        self.assertEqual(w.nonce, 3)

    def test_not_requeued_when_our_tx_was_mined(self):
        w = self.approved()
        eth = FakeEth(latest=100, pending_nonce=2)
        wd.broadcast(w, FakeWeb3(eth))
        w.refresh_from_db()
        Withdrawal.objects.filter(pk=w.pk).update(status=Withdrawal.Status.STUCK)
        eth.mined_nonce = 3
        eth.receipts[w.tx_id] = {'status': 1, 'blockNumber': 90}
        self.assertEqual(wd.check_broadcast_withdrawals(self.chain, FakeWeb3(eth))['confirmed'], 1)

    def test_rebroadcast_gas_price_is_capped(self):
        w = self.approved()
        eth = FakeEth(gas_price=1000)
        wd.broadcast(w, FakeWeb3(eth))
        eth.gas_price = 100
        Withdrawal.objects.filter(pk=w.pk).update(status=Withdrawal.Status.STUCK)
        wd.rebroadcast(w, FakeWeb3(eth))
        w.refresh_from_db()
        self.assertEqual(int(w.gas_price), 200)  # min(1000 * 1.125, 2 * current) = 200

    def test_ambiguous_send_error_keeps_broadcast(self):
        w = self.approved()
        eth = FakeEth(send_error=TimeoutError('read timeout'))
        self.assertFalse(wd.broadcast(w, FakeWeb3(eth)))  # not confirmed sent, but kept
        w.refresh_from_db()
        self.assertEqual(w.status, Withdrawal.Status.BROADCAST)
        self.assertIsNotNone(w.tx_id)

    def test_missing_chain_id_refuses_to_sign(self):
        self.chain.chain_id = None
        self.chain.save()
        w = self.approved()
        with self.assertRaises(wd.BroadcastError):
            wd.broadcast(w, FakeWeb3(FakeEth()))
        w.refresh_from_db()
        self.assertEqual(w.status, Withdrawal.Status.APPROVED)

    def test_confirmation_and_failure_via_receipts(self):
        ok = self.approved()
        bad = self.approved()
        eth = FakeEth(latest=100, pending_nonce=0)
        eth_w3 = FakeWeb3(eth)
        wd.broadcast(ok, eth_w3)
        eth.pending_nonce = 1
        wd.broadcast(bad, eth_w3)
        ok.refresh_from_db()
        bad.refresh_from_db()
        eth.receipts[ok.tx_id] = {'status': 1, 'blockNumber': 98}
        eth.receipts[bad.tx_id] = {'status': 0, 'blockNumber': 97}

        # Final block = latest - confirm_times = 97: only "bad" is final so far.
        self.assertEqual(wd.check_broadcast_withdrawals(self.chain, eth_w3),
                         {'confirmed': 0, 'failed': 1, 'stuck': 0, 'requeued': 0})
        eth.latest = 101
        self.assertEqual(wd.check_broadcast_withdrawals(self.chain, eth_w3),
                         {'confirmed': 1, 'failed': 0, 'stuck': 0, 'requeued': 0})

        ok.refresh_from_db()
        bad.refresh_from_db()
        self.assertEqual(ok.status, Withdrawal.Status.CONFIRMED)
        self.assertEqual(ok.transaction_log.status, TransactionLog.Status.CONFIRMED)
        self.assertEqual(ok.transaction_log.block_number, 98)
        self.assertEqual(bad.status, Withdrawal.Status.FAILED)
        self.assertEqual(bad.transaction_log.status, TransactionLog.Status.FAILED)
        self.assertEqual(self.balance(self.native), 4 * ETH)  # 5 - 1 confirmed; failed one refunded

    def test_stuck_then_rebroadcast_with_same_nonce(self):
        w = self.approved()
        eth = FakeEth(latest=100, pending_nonce=2, gas_price=100)
        wd.broadcast(w, FakeWeb3(eth))
        Withdrawal.objects.filter(pk=w.pk).update(broadcast_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(wd.check_broadcast_withdrawals(self.chain, FakeWeb3(eth))['stuck'], 1)

        w.refresh_from_db()
        first_tx = w.tx_id
        self.assertTrue(wd.rebroadcast(w, FakeWeb3(eth)))
        w.refresh_from_db()
        self.assertEqual(w.nonce, 2)
        self.assertGreaterEqual(int(w.gas_price), 113)
        self.assertEqual(w.previous_tx_ids, [first_tx])
        self.assertNotEqual(w.tx_id, first_tx)

        # The original transaction is the one that got mined
        eth.receipts[first_tx] = {'status': 1, 'blockNumber': 90}
        wd.check_broadcast_withdrawals(self.chain, FakeWeb3(eth))
        w.refresh_from_db()
        self.assertEqual((w.status, w.tx_id), (Withdrawal.Status.CONFIRMED, first_tx))

    def test_token_withdrawal_needs_transfer_log(self):
        from .scanner import TRANSFER_TOPIC

        def topic(a):
            return '0x' + '0' * 24 + a.lower()[2:]

        silent = self.approved(self.usdt, 1_000_000)
        real = self.approved(self.usdt, 2_000_000)
        eth = FakeEth(latest=100)
        wd.broadcast(silent, FakeWeb3(eth))
        eth.pending_nonce = 1
        wd.broadcast(real, FakeWeb3(eth))
        silent.refresh_from_db()
        real.refresh_from_db()
        eth.receipts[silent.tx_id] = {'status': 1, 'blockNumber': 90, 'logs': []}  # token returned false
        eth.receipts[real.tx_id] = {'status': 1, 'blockNumber': 90, 'logs': [{
            'address': TOKEN, 'topics': [TRANSFER_TOPIC, topic(self.hot.address), topic(DEST)],
            'data': hex(2_000_000)}]}
        self.assertEqual(wd.check_broadcast_withdrawals(self.chain, FakeWeb3(eth)),
                         {'confirmed': 1, 'failed': 1, 'stuck': 0, 'requeued': 0})
        silent.refresh_from_db()
        self.assertEqual(silent.status, Withdrawal.Status.FAILED)
        self.assertEqual(self.balance(self.usdt), 1_000_000_000 - 2_000_000)

    def test_finalized_tag_used_for_withdrawal_confirmation(self):
        self.chain.use_finalized_block = True
        self.chain.save()
        w = self.approved()
        eth = FakeEth(latest=1000)
        w3 = FakeWeb3(eth)
        w3.manager = mock.Mock()
        w3.manager.request_blocking.return_value = {'number': hex(50)}
        wd.broadcast(w, w3)
        w.refresh_from_db()
        eth.receipts[w.tx_id] = {'status': 1, 'blockNumber': 60}
        self.assertEqual(wd.check_broadcast_withdrawals(self.chain, w3)['confirmed'], 0)
        w3.manager.request_blocking.return_value = {'number': hex(60)}
        self.assertEqual(wd.check_broadcast_withdrawals(self.chain, w3)['confirmed'], 1)


@override_settings(WALLET_ENCRYPTION_KEYS=[TEST_KEY])
class WithdrawalApiTest(APITestCase):
    setUp = WithdrawalTestBase.setUp
    set_balance = WithdrawalTestBase.set_balance
    balance = WithdrawalTestBase.balance

    def test_create_and_list(self):
        self.client.force_authenticate(self.user)
        response = self.client.post('/blockchain/withdrawals/',
                                    {'coin_info': self.usdt.pk, 'to_address': DEST, 'amount': '12.5'}, format='json')
        data = response.json()
        self.assertEqual(data['amount'], '12.5')
        self.assertEqual(data['symbol'], 'USDT')
        self.assertEqual(data['status'], Withdrawal.Status.PENDING_REVIEW)
        self.assertEqual(self.balance(self.usdt), 1_000_000_000 - 12_500_000)

        other = User.objects.create_user(email='o@test.com', password='pw')
        self.client.force_authenticate(other)
        self.assertEqual(self.client.get('/blockchain/withdrawals/').json()['count'], 0)
        self.assertEqual(self.client.get(f"/blockchain/withdrawals/{data['id']}/").status_code, 200)  # handler maps 404 -> 200
        self.assertFalse(self.client.get(f"/blockchain/withdrawals/{data['id']}/").json().get('success', True))

        self.client.force_authenticate(self.user)
        listing = self.client.get('/blockchain/withdrawals/').json()
        self.assertEqual(listing['count'], 1)

    def test_errors_use_error_codes(self):
        self.client.force_authenticate(self.user)
        response = self.client.post('/blockchain/withdrawals/',
                                    {'coin_info': self.usdt.pk, 'to_address': DEST, 'amount': '999999'}, format='json')
        self.assertEqual(response.json()['error'], 'insufficient_balance')
        response = self.client.post('/blockchain/withdrawals/',
                                    {'coin_info': self.usdt.pk, 'to_address': 'nope', 'amount': '1'}, format='json')
        self.assertEqual(response.json()['error'], 'invalid_address')


@override_settings(WALLET_ENCRYPTION_KEYS=[TEST_KEY])
class WithdrawalAdminTest(TestCase):
    setUp = WithdrawalTestBase.setUp
    set_balance = WithdrawalTestBase.set_balance

    def test_admin_approve_and_create_hot_wallet(self):
        admin_user = User.objects.create_superuser(email='admin@test.com', password='pw')
        self.client.force_login(admin_user)
        w = wd.request_withdrawal(self.user, self.native, DEST, 1)
        self.client.post('/admin/blockchain/withdrawal/', {'action': 'approve_selected', '_selected_action': [w.pk]})
        w.refresh_from_db()
        self.assertEqual((w.status, w.reviewed_by), (Withdrawal.Status.APPROVED, admin_user))

        chain2 = Blockchain.objects.create(name='BSC', chain_id=56)
        self.client.post('/admin/blockchain/hotwallet/add/', {'blockchain': chain2.pk})
        hot = HotWallet.objects.get(blockchain=chain2)
        from eth_account import Account
        self.assertEqual(Account.from_key(hot.get_private_key()).address, hot.address)
        page = self.client.get(f'/admin/blockchain/hotwallet/{hot.pk}/change/')
        self.assertNotContains(page, hot.encrypted_private_key)


@override_settings(WALLET_ENCRYPTION_KEYS=[TEST_KEY])
class HardeningTest(TestCase):
    setUp = WithdrawalTestBase.setUp
    set_balance = WithdrawalTestBase.set_balance

    def test_rotation_includes_hot_wallets(self):
        import os
        from django.core.management import call_command
        new_key = 'mF0r7tq9nQ2l8gZ1cV6bX3yK5wE4aR0sD7fH2jL9pO8='
        with override_settings(WALLET_ENCRYPTION_KEYS=[new_key, TEST_KEY]):
            call_command('rotate_wallet_keys', stdout=open(os.devnull, 'w'))
        with override_settings(WALLET_ENCRYPTION_KEYS=[new_key]):
            HotWallet.objects.get().get_private_key()
            CryptoAddress.objects.get().get_private_key()

    def test_blockchain_api_is_read_only_for_users(self):
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(self.user)
        self.assertEqual(client.get(f'/blockchain/{self.chain.pk}/').status_code, 200)
        for method in ('patch', 'delete'):
            response = getattr(client, method)(f'/blockchain/{self.chain.pk}/', {'gas_limit': 1}, format='json')
            self.assertEqual(response.json().get('error'), 'permission_denied', method)
        self.assertEqual(client.post('/blockchain/', {'name': 'x'}, format='json').json().get('error'), 'permission_denied')
        self.chain.refresh_from_db()
        self.assertEqual(self.chain.gas_limit, 21000)

    def test_chain_with_hot_wallet_or_history_cannot_be_deleted(self):
        from django.db.models import ProtectedError
        with self.assertRaises(ProtectedError):
            self.chain.delete()
        with self.assertRaises(ProtectedError):
            self.user.delete()  # would otherwise delete the encrypted deposit key
