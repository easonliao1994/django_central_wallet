from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APITestCase

from .models import Blockchain, Coin, CoinInfo, CoinInfoBalance, CryptoAddress, InternalTransfer, TransactionLog, Withdrawal
from .wallet import get_or_create_deposit_address

User = get_user_model()
TEST_KEY = 'q2zQm1p2kqgk9b6r3XyUoJmYyq1x0n8nqQ3sV5hY6uE='
USDC = 10 ** 6


@override_settings(WALLET_ENCRYPTION_KEYS=[TEST_KEY])
class TransferTestBase(APITestCase):
    def setUp(self):
        self.eth = Coin.objects.create(symbol='ETH', name='Ethereum')
        self.usdc = Coin.objects.create(symbol='USDC', name='USD Coin', scale=2)
        self.chain = Blockchain.objects.create(name='Sepolia', chain_id=11155111, coin=self.eth)
        self.native = CoinInfo.objects.create(blockchain=self.chain, coin=self.eth, decimal=18)
        self.coin = CoinInfo.objects.create(blockchain=self.chain, coin=self.usdc, decimal=6, contract='0x' + 'aa' * 20)
        self.alice = User.objects.create_user(email='alice@test.com', password='pw', is_verified=True)
        self.bob = User.objects.create_user(email='bob@test.com', password='pw', is_verified=True)
        self.alice_addr = get_or_create_deposit_address(self.alice)
        self.bob_addr = get_or_create_deposit_address(self.bob)
        self.set_balance(self.alice_addr, external=100 * USDC)

    def set_balance(self, address, external=0, internal=0, coin_info=None):
        CoinInfoBalance.objects.update_or_create(crypto_address=address, coin_info=coin_info or self.coin,
                                                 defaults={'external_amount': external, 'internal_amount': internal})

    def balance(self, address, coin_info=None):
        b = CoinInfoBalance.objects.get(crypto_address=address, coin_info=coin_info or self.coin)
        return int(b.internal_amount), int(b.external_amount)

    def send(self, to, amount='12.5', user=None, coin_info=None, memo=''):
        self.client.force_authenticate(user or self.alice)
        return self.client.post('/blockchain/transfers/', {'coin_info': (coin_info or self.coin).pk, 'to': to,
                                                           'amount': amount, 'memo': memo}, format='json').json()


class InternalTransferApiTest(TransferTestBase):
    def test_transfer_by_email_moves_internal_balance_only(self):
        data = self.send('BOB@test.com', memo='lunch')
        self.assertEqual((data['type'], data['flow'], data['symbol'], data['amount'], data['memo']),
                         ('internal_transfer', 'out', 'USDC', '12.5', 'lunch'))
        self.assertEqual(self.balance(self.alice_addr), (-12_500_000, 100 * USDC))  # total 87.5
        self.assertEqual(self.balance(self.bob_addr), (12_500_000, 0))
        # No on-chain activity: no withdrawal, nothing for the scanner or sweeps.
        self.assertEqual(Withdrawal.objects.count(), 0)

    def test_transfer_by_platform_address(self):
        data = self.send(self.bob_addr.address.upper().replace('0X', '0x'))
        self.assertEqual(data['to_address'], self.bob_addr.address.lower())
        self.assertEqual(self.balance(self.bob_addr), (12_500_000, 0))

    def test_both_users_see_it_in_their_history(self):
        t = InternalTransfer.objects.get(pk=self.send('bob@test.com')['id'])
        self.client.force_authenticate(self.alice)
        alice_tx = self.client.get('/blockchain/transactions/').json()['results']
        self.client.force_authenticate(self.bob)
        bob_tx = self.client.get('/blockchain/transactions/').json()['results']
        self.assertEqual([(r['direction'], r['flow'], r['amount'], r['internal_transfer']) for r in alice_tx],
                         [('internal', 'out', '12.5', t.pk)])
        self.assertEqual([(r['direction'], r['flow'], r['amount'], r['internal_transfer']) for r in bob_tx],
                         [('internal', 'in', '12.5', t.pk)])
        # And in the transfer list, from each side.
        self.assertEqual(self.client.get('/blockchain/transfers/').json()['results'][0]['flow'], 'in')
        self.assertEqual(self.client.get(f'/blockchain/transfers/{t.pk}/').json()['flow'], 'in')

    def test_balances_api_reflects_transfer(self):
        self.send('bob@test.com')
        self.client.force_authenticate(self.bob)
        rows = {r['coin_info']: r for r in self.client.get('/blockchain/balances/').json()}
        self.assertEqual(rows[self.coin.pk]['balance'], '12.5')

    def test_errors(self):
        self.assertEqual(self.send('bob@test.com', amount='100.000001')['error'], 'insufficient_balance')
        self.assertEqual(self.send('alice@test.com')['error'], 'cannot_transfer_to_self')
        self.assertEqual(self.send(self.alice_addr.address)['error'], 'cannot_transfer_to_self')
        self.assertEqual(self.send('nobody@test.com')['error'], 'recipient_not_found')
        self.assertEqual(self.send('0x' + '99' * 20)['error'], 'recipient_not_found')  # not a platform address
        self.assertEqual(self.send('bob@test.com', amount='0')['error'], 'invalid_amount')
        self.assertEqual(self.send('bob@test.com', amount='1.0000001')['error'], 'invalid_amount')  # > 6 decimals
        unverified = User.objects.create_user(email='new@test.com', password='pw', is_verified=False, is_active=False)
        self.assertEqual(self.send('new@test.com')['error'], 'recipient_not_found')
        self.assertEqual(self.send('bob@test.com', user=unverified)['error'], 'email_not_verified')
        self.assertEqual(self.balance(self.alice_addr), (0, 100 * USDC))  # nothing moved
        self.assertEqual(TransactionLog.objects.filter(direction='internal').count(), 0)

    def test_debits_an_address_that_holds_enough(self):
        older = CryptoAddress(user=self.alice, address='0x' + '12' * 20)
        older.set_private_key('0x' + '01' * 32)
        older.save()
        self.set_balance(self.alice_addr, external=5 * USDC)
        self.set_balance(older, external=50 * USDC)
        self.send('bob@test.com', amount='20')
        self.assertEqual(self.balance(older), (-20 * USDC, 50 * USDC))
        self.assertEqual(self.balance(self.alice_addr), (0, 5 * USDC))

    def test_received_funds_can_be_sent_on(self):
        self.send('bob@test.com', amount='30')
        data = self.send('alice@test.com', amount='30', user=self.bob)
        self.assertEqual(data['flow'], 'out')
        self.assertEqual(self.balance(self.bob_addr), (0, 0))
        self.assertEqual(self.balance(self.alice_addr), (0, 100 * USDC))

    def test_users_only_see_their_own_transfers(self):
        t = self.send('bob@test.com')['id']
        carol = User.objects.create_user(email='carol@test.com', password='pw', is_verified=True)
        self.client.force_authenticate(carol)
        self.assertEqual(self.client.get('/blockchain/transfers/').json()['count'], 0)
        self.assertEqual(self.client.get(f'/blockchain/transfers/{t}/').json()['success'], False)


class WithdrawalRoutingTest(TransferTestBase):
    def withdraw(self, to, amount='10'):
        self.client.force_authenticate(self.alice)
        return self.client.post('/blockchain/withdrawals/', {'coin_info': self.coin.pk, 'to_address': to,
                                                             'amount': amount}, format='json').json()

    def test_withdrawal_to_another_users_address_becomes_internal_transfer(self):
        data = self.withdraw(self.bob_addr.address)
        self.assertEqual((data['type'], data['flow'], data['amount']), ('internal_transfer', 'out', '10'))
        self.assertEqual(Withdrawal.objects.count(), 0)
        self.assertEqual(self.balance(self.bob_addr), (10 * USDC, 0))

    def test_withdrawal_to_own_address_still_rejected(self):
        self.assertEqual(self.withdraw(self.alice_addr.address)['error'], 'invalid_address')

    def test_withdrawal_to_external_address_unchanged(self):
        data = self.withdraw('0x' + '44' * 20)
        self.assertEqual(data['status'], Withdrawal.Status.PENDING_REVIEW)
        self.assertEqual(Withdrawal.objects.count(), 1)


from unittest import skipUnless

from django.db import connection
from django.test import TransactionTestCase


@skipUnless(connection.vendor == 'postgresql', 'needs real row locks')
@override_settings(WALLET_ENCRYPTION_KEYS=[TEST_KEY])
class ConcurrentTransferTest(TransactionTestCase):
    setUp = TransferTestBase.setUp  # same fixtures, but committed so other threads see them
    set_balance = TransferTestBase.set_balance
    balance = TransferTestBase.balance

    def test_parallel_transfers_never_overdraw(self):
        import threading

        from django.db import connections

        from .transfers import transfer

        errors, done = [], []

        def worker():
            try:
                transfer(User.objects.get(pk=self.alice.pk), self.coin, 'bob@test.com', 15 * USDC)
                done.append(1)
            except Exception as e:
                errors.append(type(e).__name__)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 100 USDC / 15 per transfer: exactly 6 can succeed.
        self.assertEqual((len(done), sorted(errors)), (6, ['InsufficientBalanceException'] * 4))
        internal, external = self.balance(self.alice_addr)
        self.assertEqual(internal + external, 10 * USDC)
        self.assertEqual(self.balance(self.bob_addr), (90 * USDC, 0))
