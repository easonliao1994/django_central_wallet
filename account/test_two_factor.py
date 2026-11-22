import io
from datetime import timedelta
from unittest import mock

import pyotp
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from blockchain.key_vault import decrypt_secret
from blockchain.models import Blockchain, Coin, CoinInfo, CoinInfoBalance, InternalTransfer, Withdrawal
from blockchain.wallet import get_or_create_deposit_address

from .models import WithdrawalAddress

User = get_user_model()
TEST_KEY = 'q2zQm1p2kqgk9b6r3XyUoJmYyq1x0n8nqQ3sV5hY6uE='
PASSWORD = 'Corr3ct-Horse-Battery'
USDC = 10 ** 6
EXTERNAL = '0x' + '44' * 20


@override_settings(WALLET_ENCRYPTION_KEYS=[TEST_KEY], SITE_URL='https://wallet.example.com',
                   REQUIRE_2FA_FOR_WITHDRAWAL=True)
class TwoFactorTestBase(APITestCase):
    def setUp(self):
        cache.clear()  # throttles and wrong-code counters
        eth = Coin.objects.create(symbol='ETH', name='Ethereum')
        usdc = Coin.objects.create(symbol='USDC', name='USD Coin', scale=2)
        chain = Blockchain.objects.create(name='Sepolia', chain_id=11155111, coin=eth)
        self.coin = CoinInfo.objects.create(blockchain=chain, coin=usdc, decimal=6, contract='0x' + 'aa' * 20)
        self.alice = User.objects.create_user(email='alice@test.com', password=PASSWORD, is_verified=True, is_active=True)
        self.bob = User.objects.create_user(email='bob@test.com', password=PASSWORD, is_verified=True, is_active=True)
        self.alice_addr = get_or_create_deposit_address(self.alice)
        self.bob_addr = get_or_create_deposit_address(self.bob)
        CoinInfoBalance.objects.update_or_create(crypto_address=self.alice_addr, coin_info=self.coin,
                                                 defaults={'external_amount': 100 * USDC})
        # A controllable clock for TOTP: every code() call moves to the next 30 s step.
        self.clock = 1_700_000_000.0
        patcher = mock.patch('account.two_factor._clock', side_effect=lambda: self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.secret = None

    def code(self, advance=True):
        if advance:
            self.clock += 30
        return pyotp.TOTP(self.secret).at(self.clock)

    def post(self, url, data=None, user=None):
        self.client.force_authenticate(user or self.alice)
        with self.captureOnCommitCallbacks(execute=True):  # notice emails are sent on commit
            return self.client.post(url, data or {}, format='json').json()

    def enable_2fa(self, user=None):
        user = user or self.alice
        data = self.post('/account/2fa/setup/', {'password': PASSWORD}, user)
        self.secret = data['secret']
        self.assertTrue(self.post('/account/2fa/enable/', {'otp_code': self.code()}, user)['success'])
        user.refresh_from_db()
        return data

    def withdraw(self, otp_code=None, to=EXTERNAL, amount='10'):
        payload = {'coin_info': self.coin.pk, 'to_address': to, 'amount': amount}
        if otp_code is not None:
            payload['otp_code'] = otp_code
        return self.post('/blockchain/withdrawals/', payload)

    def send(self, otp_code=None, to='bob@test.com', amount='5'):
        payload = {'coin_info': self.coin.pk, 'to': to, 'amount': amount}
        if otp_code is not None:
            payload['otp_code'] = otp_code
        return self.post('/blockchain/transfers/', payload)


class TwoFactorSetupTest(TwoFactorTestBase):
    def test_setup_and_enable(self):
        self.assertEqual(self.post('/account/2fa/setup/', {'password': 'wrong'})['error'], 'invalid_credentials')
        data = self.post('/account/2fa/setup/', {'password': PASSWORD})
        self.assertTrue(data['otpauth_uri'].startswith('otpauth://totp/'))
        self.assertIn('alice%40test.com', data['otpauth_uri'])
        self.assertIn('<svg', data['qr_code_svg'])
        self.alice.refresh_from_db()
        self.assertNotIn(data['secret'], self.alice.totp_secret)  # encrypted at rest
        self.assertEqual(decrypt_secret(self.alice.totp_secret), data['secret'])
        self.assertFalse(self.client.get('/account/me/').json()['two_factor_enabled'])  # not until confirmed

        self.secret = data['secret']
        self.assertEqual(self.post('/account/2fa/enable/', {'otp_code': '000000'})['error'], 'invalid_otp')
        mail.outbox.clear()
        result = self.post('/account/2fa/enable/', {'otp_code': self.code()})
        self.assertTrue(result['user']['two_factor_enabled'])
        self.assertIn('Two-factor authentication enabled', mail.outbox[0].subject)
        self.assertEqual(self.post('/account/2fa/setup/', {'password': PASSWORD})['error'], 'two_factor_already_enabled')

    def test_login_needs_a_code_once_enabled(self):
        self.enable_2fa()
        Token.objects.all().delete()
        self.client.force_authenticate(None)
        login = lambda **extra: self.client.post('/account/login/', {'email': 'alice@test.com', 'password': PASSWORD,
                                                                    **extra}, format='json').json()
        self.assertEqual(login()['error'], 'otp_required')
        self.assertEqual(login(otp_code='123456')['error'], 'invalid_otp')
        self.assertFalse(Token.objects.exists())
        code = self.code()
        self.assertTrue(login(otp_code=code)['token'])
        self.assertEqual(login(otp_code=code)['error'], 'invalid_otp')  # single use
        # A wrong password still says invalid_credentials, never otp_required.
        self.assertEqual(self.client.post('/account/login/', {'email': 'alice@test.com', 'password': 'nope'},
                                          format='json').json()['error'], 'invalid_credentials')

    def test_codes_from_adjacent_steps_are_accepted_but_not_older_ones(self):
        self.enable_2fa()
        self.clock += 300
        previous = pyotp.TOTP(self.secret).at(self.clock - 30)
        self.assertEqual(self.withdraw(previous)['status'], 'pending_review')  # clock drift
        too_old = pyotp.TOTP(self.secret).at(self.clock - 60)
        self.assertEqual(self.withdraw(too_old)['error'], 'invalid_otp')

    @override_settings(OTP_MAX_FAILURES=3)
    def test_wrong_codes_lock_out(self):
        self.enable_2fa()
        for _ in range(3):
            self.assertEqual(self.withdraw('111111')['error'], 'invalid_otp')
        self.assertEqual(self.withdraw(self.code())['error'], 'too_many_otp_attempts')
        cache.clear()  # the window passed
        self.assertEqual(self.withdraw(self.code())['status'], 'pending_review')

    def test_disable_needs_password_and_code_and_pauses_withdrawals(self):
        self.enable_2fa()
        self.assertEqual(self.post('/account/2fa/disable/', {'password': 'x', 'otp_code': self.code()})['error'],
                         'invalid_credentials')
        self.assertEqual(self.post('/account/2fa/disable/', {'password': PASSWORD, 'otp_code': '000000'})['error'],
                         'invalid_otp')
        mail.outbox.clear()
        data = self.post('/account/2fa/disable/', {'password': PASSWORD, 'otp_code': self.code()})
        self.assertFalse(data['user']['two_factor_enabled'])
        self.assertIsNotNone(data['user']['withdrawals_locked_until'])
        self.assertIn('disabled', mail.outbox[0].subject)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.totp_secret, '')
        self.assertEqual(self.withdraw()['error'], 'withdrawals_locked')
        with override_settings(REQUIRE_2FA_FOR_WITHDRAWAL=False):
            self.assertEqual(self.send()['error'], 'withdrawals_locked')


class OutgoingFundsTest(TwoFactorTestBase):
    def test_withdrawals_and_transfers_need_2fa_by_default(self):
        self.assertEqual(self.withdraw()['error'], 'two_factor_required')
        self.assertEqual(self.send()['error'], 'two_factor_required')
        with override_settings(REQUIRE_2FA_FOR_WITHDRAWAL=False):
            self.assertEqual(self.withdraw()['status'], 'pending_review')

    def test_withdrawal_needs_a_valid_code_and_sends_a_notice(self):
        self.enable_2fa()
        self.assertEqual(self.withdraw()['error'], 'otp_required')
        self.assertEqual(self.withdraw('000000')['error'], 'invalid_otp')
        self.assertEqual(Withdrawal.objects.count(), 0)
        mail.outbox.clear()
        code = self.code()
        self.assertEqual(self.withdraw(code)['status'], 'pending_review')
        self.assertEqual(self.withdraw(code)['error'], 'invalid_otp')  # replay
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Withdrawal requested', mail.outbox[0].subject)
        self.assertIn(EXTERNAL.lower(), mail.outbox[0].body.lower())

    def test_a_failed_withdrawal_does_not_use_up_the_code(self):
        self.enable_2fa()
        code = self.code()
        self.assertEqual(self.withdraw(code, amount='1000')['error'], 'insufficient_balance')
        self.assertEqual(self.withdraw(code)['status'], 'pending_review')

    def test_transfer_needs_a_code_and_sends_a_notice(self):
        self.enable_2fa()
        self.assertEqual(self.send()['error'], 'otp_required')
        mail.outbox.clear()
        data = self.send(self.code())
        self.assertEqual((data['type'], data['amount']), ('internal_transfer', '5'))
        self.assertIn('Transfer sent', mail.outbox[0].subject)
        self.assertIn('bob@test.com', mail.outbox[0].body)

    def test_password_reset_pauses_withdrawals(self):
        self.enable_2fa()
        self.client.force_authenticate(None)
        response = self.client.post('/account/password/reset/', {
            'uid': urlsafe_base64_encode(force_bytes(self.alice.pk)),
            'token': default_token_generator.make_token(self.alice),
            'new_password': 'An0ther-Str0ng-Pass'}, format='json').json()
        self.assertTrue(response['success'])
        self.alice.refresh_from_db()
        self.assertGreater(self.alice.withdrawals_locked_until, timezone.now() + timedelta(hours=23))
        self.assertEqual(self.withdraw(self.code())['error'], 'withdrawals_locked')
        User.objects.filter(pk=self.alice.pk).update(withdrawals_locked_until=timezone.now() - timedelta(seconds=1))
        self.alice.refresh_from_db()
        self.assertEqual(self.withdraw(self.code())['status'], 'pending_review')


class WhitelistTest(TwoFactorTestBase):
    def add_address(self, address=EXTERNAL, label='cold', code=None):
        return self.post('/account/withdrawal-addresses/',
                         {'address': address, 'label': label, 'otp_code': code or self.code()})

    def activate_all(self):
        WithdrawalAddress.objects.update(active_from=timezone.now() - timedelta(seconds=1))

    def test_adding_an_address_needs_2fa_and_a_code(self):
        self.assertEqual(self.add_address(code='123456')['error'], 'two_factor_required')
        self.enable_2fa()
        self.assertEqual(self.add_address(code='000000')['error'], 'invalid_otp')
        self.assertEqual(self.add_address(address='0x1234')['error'], 'invalid_address')
        mail.outbox.clear()
        data = self.add_address(address=EXTERNAL.lower())
        self.assertEqual(data['address'], '0x4444444444444444444444444444444444444444')
        self.assertFalse(data['active'])  # cooldown
        self.assertIn('New withdrawal address', mail.outbox[0].subject)
        self.assertEqual(self.add_address()['error'], 'address_already_saved')
        listing = self.client.get('/account/withdrawal-addresses/').json()
        self.assertEqual([a['label'] for a in listing], ['cold'])
        self.client.force_authenticate(self.bob)
        self.assertEqual(self.client.get('/account/withdrawal-addresses/').json(), [])  # own addresses only
        self.assertEqual(self.client.delete(f"/account/withdrawal-addresses/{data['id']}/").status_code, 200)  # 404
        self.assertTrue(WithdrawalAddress.objects.exists())
        self.client.force_authenticate(self.alice)
        self.assertEqual(self.client.delete(f"/account/withdrawal-addresses/{data['id']}/").status_code, 204)
        self.assertFalse(WithdrawalAddress.objects.exists())

    def test_whitelist_mode_limits_withdrawals_to_active_saved_addresses(self):
        self.enable_2fa()
        self.add_address()
        data = self.post('/account/withdrawal-whitelist/', {'enabled': True})
        self.assertTrue(data['user']['withdrawal_whitelist_enabled'])
        self.assertEqual(self.withdraw(self.code(), to='0x' + '55' * 20)['error'], 'address_not_whitelisted')
        self.assertEqual(self.withdraw(self.code())['error'], 'address_not_whitelisted')  # saved, still cooling down
        self.activate_all()
        self.assertEqual(self.withdraw(self.code(), to=EXTERNAL.lower())['status'], 'pending_review')

    def test_whitelist_applies_to_transfers_and_routed_withdrawals(self):
        self.enable_2fa()
        self.post('/account/withdrawal-whitelist/', {'enabled': True})
        CoinInfo.objects.filter(pk=self.coin.pk).update(auto_approve_withdrawal_limit=50 * USDC)
        self.assertEqual(self.send(self.code())['error'], 'address_not_whitelisted')
        self.assertEqual(self.withdraw(self.code(), to=self.bob_addr.address)['error'], 'address_not_whitelisted')
        self.add_address(address=self.bob_addr.address, label='bob')
        self.activate_all()
        self.assertEqual(self.send(self.code())['type'], 'internal_transfer')
        self.assertEqual(self.withdraw(self.code(), to=self.bob_addr.address)['type'], 'internal_transfer')
        self.assertEqual(InternalTransfer.objects.count(), 2)

    def test_turning_whitelist_mode_off_takes_the_cooldown(self):
        self.enable_2fa()
        self.assertEqual(self.post('/account/withdrawal-whitelist/', {'enabled': True}, self.bob)['error'],
                         'two_factor_required')
        self.post('/account/withdrawal-whitelist/', {'enabled': True})
        self.assertEqual(self.post('/account/withdrawal-whitelist/', {'enabled': False})['error'], 'otp_required')
        data = self.post('/account/withdrawal-whitelist/', {'enabled': False, 'otp_code': self.code()})
        self.assertTrue(data['user']['withdrawal_whitelist_enabled'])  # still in force
        self.assertIsNotNone(data['user']['whitelist_disable_at'])
        self.assertEqual(self.withdraw(self.code())['error'], 'address_not_whitelisted')
        # Turning it on again cancels the switch-off.
        data = self.post('/account/withdrawal-whitelist/', {'enabled': True})
        self.assertIsNone(data['user']['whitelist_disable_at'])
        self.post('/account/withdrawal-whitelist/', {'enabled': False, 'otp_code': self.code()})
        User.objects.filter(pk=self.alice.pk).update(
            whitelist_disable_requested_at=timezone.now() - timedelta(hours=25))
        self.alice.refresh_from_db()
        self.assertFalse(self.client.get('/account/me/').json()['withdrawal_whitelist_enabled'])
        self.assertEqual(self.withdraw(self.code())['status'], 'pending_review')


class TwoFactorKeyRotationTest(TwoFactorTestBase):
    def test_rotate_wallet_keys_re_encrypts_2fa_secrets(self):
        from cryptography.fernet import Fernet
        self.enable_2fa()
        new_key = Fernet.generate_key().decode()
        with override_settings(WALLET_ENCRYPTION_KEYS=[new_key, TEST_KEY]):
            out = io.StringIO()
            call_command('rotate_wallet_keys', stdout=out)
        self.assertIn('2FA secrets: 1', out.getvalue())
        self.alice.refresh_from_db()
        with override_settings(WALLET_ENCRYPTION_KEYS=[new_key]):
            self.assertEqual(decrypt_secret(self.alice.totp_secret), self.secret)
