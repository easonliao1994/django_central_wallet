from datetime import datetime
from unittest import skipUnless

from django.db import connection

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from django_central_wallet.utils.global_method import format_amount

from .models import Blockchain, Coin, CoinInfo, CoinInfoBalance, CryptoAddress, TransactionLog, Withdrawal

User = get_user_model()
TEST_KEY = 'q2zQm1p2kqgk9b6r3XyUoJmYyq1x0n8nqQ3sV5hY6uE='
ETH = 10 ** 18


class FormatAmountTest(SimpleTestCase):
    def test_formats_and_rounds_down_to_scale(self):
        self.assertEqual(format_amount(1_500_000, 6), '1.5')
        self.assertEqual(format_amount(0, 18, 8), '0')
        self.assertEqual(format_amount(100 * ETH, 18, 8), '100')
        self.assertEqual(format_amount(1_999_999_999_999_999_999, 18, 8), '1.99999999')  # never rounds up
        self.assertEqual(format_amount(1, 18, 8), '0')
        self.assertEqual(format_amount(1, 18), '0.000000000000000001')


@override_settings(WALLET_ENCRYPTION_KEYS=[TEST_KEY])
class ApiTestBase(APITestCase):
    def setUp(self):
        self.eth = Coin.objects.create(symbol='ETH', name='Ethereum', scale=8)
        self.usdc = Coin.objects.create(symbol='USDC', name='USD Coin', scale=2)
        self.mainnet = Blockchain.objects.create(name='Ethereum', chain_id=1, coin=self.eth,
                                                 block_explorer_url='https://etherscan.io')
        self.sepolia = Blockchain.objects.create(name='Sepolia', chain_id=11155111, coin=self.eth)
        self.eth_main = CoinInfo.objects.create(blockchain=self.mainnet, coin=self.eth, decimal=18)
        self.usdc_main = CoinInfo.objects.create(blockchain=self.mainnet, coin=self.usdc, decimal=6, contract='0x' + 'aa' * 20)
        self.eth_sepolia = CoinInfo.objects.create(blockchain=self.sepolia, coin=self.eth, decimal=18)

        self.user = User.objects.create_user(email='a@test.com', password='pw', is_verified=True)
        self.other = User.objects.create_user(email='b@test.com', password='pw', is_verified=True)
        self.admin = User.objects.create_superuser(email='admin@test.com', password='pw')
        self.addr = self.address(self.user, '11')
        self.addr2 = self.address(self.user, '12')  # older accounts can have several addresses
        self.other_addr = self.address(self.other, '22')

    def address(self, user, byte):
        a = CryptoAddress(user=user, address='0x' + byte * 20)
        a.set_private_key('0x' + '01' * 32)
        a.save()
        return a

    def set_balance(self, address, coin_info, external=0, internal=0):
        CoinInfoBalance.objects.update_or_create(crypto_address=address, coin_info=coin_info,
                                                 defaults={'external_amount': external, 'internal_amount': internal})

    def get(self, url, as_user=None, **params):
        self.client.force_authenticate(as_user or self.user)
        return self.client.get(url, params).json()


class BalanceApiTest(ApiTestBase):
    url = '/blockchain/balances/'

    def by_coin(self, data):
        return {row['coin_info']: row for row in data}

    def test_my_balances_per_chain_and_coin(self):
        self.set_balance(self.addr, self.eth_main, external=ETH, internal=ETH // 2)
        self.set_balance(self.addr2, self.eth_main, external=ETH // 4)
        self.set_balance(self.addr, self.usdc_main, external=12_345_678)  # 12.345678 -> scale 2
        self.set_balance(self.other_addr, self.eth_main, external=50 * ETH)

        rows = self.by_coin(self.get(self.url))
        self.assertEqual(set(rows), {self.eth_main.pk, self.usdc_main.pk, self.eth_sepolia.pk})
        eth = rows[self.eth_main.pk]
        self.assertEqual((eth['blockchain'], eth['symbol'], eth['contract']), ('Ethereum', 'ETH', None))
        self.assertEqual(eth['balance_raw'], str(ETH + ETH // 2 + ETH // 4))
        self.assertEqual(eth['balance'], '1.75')  # both addresses, internal + external, rounded down
        self.assertEqual(rows[self.usdc_main.pk]['balance'], '12.34')
        self.assertEqual((rows[self.eth_sepolia.pk]['balance'], rows[self.eth_sepolia.pk]['balance_raw']), ('0', '0'))

    @skipUnless(connection.vendor == 'postgresql', 'SQLite reads decimals with 15 significant digits')
    def test_balances_are_exact_to_the_smallest_unit(self):
        self.set_balance(self.addr, self.eth_main, external=ETH + 123, internal=ETH // 2)
        self.set_balance(self.addr2, self.eth_main, external=10 ** 27 + 1)
        rows = self.by_coin(self.get(self.url))
        self.assertEqual(rows[self.eth_main.pk]['balance_raw'], str(ETH + 123 + ETH // 2 + 10 ** 27 + 1))

    def test_filters(self):
        self.assertEqual([r['coin_info'] for r in self.get(self.url, blockchain=self.sepolia.pk)], [self.eth_sepolia.pk])
        self.assertEqual({r['coin_info'] for r in self.get(self.url, symbol='eth')}, {self.eth_main.pk, self.eth_sepolia.pk})
        self.assertEqual(self.get(self.url, blockchain='abc')['error'], 'invalid_filter')

    def test_users_cannot_see_others_but_superusers_can(self):
        self.set_balance(self.addr, self.eth_main, external=ETH)
        self.set_balance(self.other_addr, self.eth_main, external=2 * ETH)
        mine = self.by_coin(self.get(self.url, user=self.other.pk))
        self.assertEqual(mine[self.eth_main.pk]['balance'], '1')  # ?user is ignored for normal users

        self.client.force_authenticate(self.admin)
        rows = self.by_coin(self.client.get(self.url, {'user': self.other.pk}).json())
        self.assertEqual(rows[self.eth_main.pk]['balance'], '2')
        totals = self.by_coin(self.client.get(self.url, {'user': 'all'}).json())
        self.assertEqual(totals[self.eth_main.pk]['balance'], '3')  # platform total

    def test_requires_authentication(self):
        self.assertEqual(self.client.get(self.url).json()['success'], False)


class TransactionApiTest(ApiTestBase):
    url = '/blockchain/transactions/'

    def log(self, user, coin_info, direction=TransactionLog.Direction.DEPOSIT, status=TransactionLog.Status.CONFIRMED,
            amount=ETH, when=None, tx_id=None, **kw):
        t = TransactionLog.objects.create(user=user, blockchain=coin_info.blockchain, coin_info=coin_info,
                                          direction=direction, status=status, amount=amount, tx_id=tx_id, **kw)
        if when:
            TransactionLog.objects.filter(pk=t.pk).update(created_at=when)
        return t

    def ids(self, data):
        return [row['id'] for row in data['results']]

    def at(self, day, hour=12):
        return timezone.make_aware(datetime(2026, 11, day, hour))

    def test_my_history_newest_first_with_details(self):
        d = self.log(self.user, self.eth_main, amount=ETH * 3 // 2, tx_id='0x' + 'ab' * 32, when=self.at(1),
                     sender_address='0x' + '33' * 20, recipient_address=self.addr.address, block_number=99)
        w_log = self.log(self.user, self.usdc_main, direction='withdrawal', status='pending', amount=2_500_000,
                         when=self.at(2))
        Withdrawal.objects.create(user=self.user, crypto_address=self.addr, coin_info=self.usdc_main,
                                  to_address='0x' + '44' * 20, amount=2_500_000, transaction_log=w_log)
        self.log(self.other, self.eth_main)  # someone else's
        self.log(None, self.eth_main, direction='sweep')  # platform-internal

        data = self.get(self.url)
        self.assertEqual((data['success'], data['count']), (True, 2))
        newest, oldest = data['results']
        self.assertEqual((newest['id'], newest['direction'], newest['symbol'], newest['amount']),
                         (w_log.pk, 'withdrawal', 'USDC', '2.5'))
        self.assertEqual((newest['withdrawal_status'], newest['tx_url']), (Withdrawal.Status.PENDING_REVIEW, None))
        self.assertEqual((oldest['id'], oldest['amount'], oldest['amount_raw']), (d.pk, '1.5', str(ETH * 3 // 2)))
        self.assertEqual((oldest['from_address'], oldest['block_number'], oldest['withdrawal']), ('0x' + '33' * 20, 99, None))
        self.assertEqual(oldest['tx_url'], 'https://etherscan.io/tx/0x' + 'ab' * 32)

    def test_filters(self):
        a = self.log(self.user, self.eth_main, when=self.at(1))
        b = self.log(self.user, self.usdc_main, direction='withdrawal', status='failed', when=self.at(2, 23))
        c = self.log(self.user, self.eth_sepolia, direction='internal', when=self.at(3))
        self.assertEqual(self.ids(self.get(self.url, blockchain=self.sepolia.pk)), [c.pk])
        self.assertEqual(self.ids(self.get(self.url, coin_info=self.usdc_main.pk)), [b.pk])
        self.assertEqual(self.ids(self.get(self.url, symbol='eth')), [c.pk, a.pk])
        self.assertEqual(self.ids(self.get(self.url, direction='deposit,internal')), [c.pk, a.pk])
        self.assertEqual(self.ids(self.get(self.url, status='failed')), [b.pk])
        # A bare upper-bound date includes that whole day.
        self.assertEqual(self.ids(self.get(self.url, created_after='2026-11-02', created_before='2026-11-02')), [b.pk])
        self.assertEqual(self.ids(self.get(self.url, created_before='2026-11-02T12:00:00')), [a.pk])
        for bad in ({'direction': 'refund'}, {'status': 'done'}, {'created_after': 'yesterday'}, {'coin_info': 'x'}):
            self.assertEqual(self.get(self.url, **bad)['error'], 'invalid_filter')

    def test_pagination(self):
        for i in range(12):
            self.log(self.user, self.eth_main, when=self.at(1, i))
        first = self.get(self.url, page_size=5)
        self.assertEqual((first['count'], len(first['results']), first['next']), (12, 5, True))
        last = self.get(self.url, page_size=5, page=3)
        self.assertEqual((len(last['results']), last['next']), (2, False))

    def test_retrieve_only_my_own(self):
        mine = self.log(self.user, self.eth_main)
        theirs = self.log(self.other, self.eth_main)
        self.assertEqual(self.get(f'{self.url}{mine.pk}/')['id'], mine.pk)
        self.assertEqual(self.get(f'{self.url}{theirs.pk}/')['success'], False)

    def test_superuser_sees_everything(self):
        mine = self.log(self.user, self.eth_main)
        theirs = self.log(self.other, self.eth_main)
        sweep = self.log(None, self.eth_main, direction='sweep')
        self.assertEqual(self.ids(self.get(self.url, as_user=self.admin, user='all')), [sweep.pk, theirs.pk, mine.pk])
        self.assertEqual(self.ids(self.get(self.url, as_user=self.admin, user=self.other.pk)), [theirs.pk])
        self.assertEqual(self.get(f'{self.url}{theirs.pk}/', as_user=self.admin, user='all')['id'], theirs.pk)
        # Normal users cannot widen their view.
        self.assertEqual(self.ids(self.get(self.url, user='all')), [mine.pk])
