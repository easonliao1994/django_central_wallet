import re

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

User = get_user_model()
TEST_WALLET_KEY = 'q2zQm1p2kqgk9b6r3XyUoJmYyq1x0n8nqQ3sV5hY6uE='
PASSWORD = 'Corr3ct-Horse-Battery'


@override_settings(WALLET_ENCRYPTION_KEYS=[TEST_WALLET_KEY], SITE_URL='https://wallet.example.com')
class AuthApiTest(APITestCase):
    def setUp(self):
        cache.clear()  # throttle counters
        self.user = User.objects.create_user(email='alice@test.com', password=PASSWORD, first_name='Alice',
                                             last_name='A', is_verified=True, is_active=True)

    def post(self, url, data=None, token=None):
        if token:
            self.client.credentials(HTTP_AUTHORIZATION=f'Token {token}')
        else:
            self.client.credentials()
        return self.client.post(url, data or {}, format='json').json()

    def login(self, email='alice@test.com', password=PASSWORD):
        return self.post('/account/login/', {'email': email, 'password': password})

    # ------------------------------------------------------------------ login / logout
    def test_login_returns_token_that_authenticates(self):
        data = self.login(email='ALICE@test.com')  # email is case-insensitive
        self.assertEqual((data['success'], data['user']['email']), (True, 'alice@test.com'))
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {data['token']}")
        self.assertEqual(self.client.get('/account/me/').json()['email'], 'alice@test.com')
        self.assertEqual(self.login()['token'], data['token'])  # same token on the next login

    def test_login_errors(self):
        self.assertEqual(self.login(password='wrong')['error'], 'invalid_credentials')
        self.assertEqual(self.login(email='nobody@test.com')['error'], 'invalid_credentials')
        User.objects.create_user(email='new@test.com', password=PASSWORD, is_active=False, is_verified=False)
        self.assertEqual(self.login(email='new@test.com')['error'], 'email_not_verified')
        User.objects.filter(email='alice@test.com').update(is_active=False)
        self.assertEqual(self.login()['error'], 'account_disabled')
        # Unverified accounts are only distinguishable with the right password.
        self.assertEqual(self.login(email='new@test.com', password='wrong')['error'], 'invalid_credentials')

    def test_logout_revokes_token(self):
        token = self.login()['token']
        self.assertEqual(self.post('/account/logout/', token=token)['success'], True)
        self.assertFalse(Token.objects.filter(key=token).exists())
        self.assertEqual(self.client.get('/account/me/').json()['success'], False)

    @override_settings(REST_FRAMEWORK={
        'EXCEPTION_HANDLER': 'django_central_wallet.utils.custom_exception_handler.custom_exception_handler',
        'DEFAULT_THROTTLE_RATES': {'auth': '3/hour'},
    })
    def test_login_is_throttled(self):
        from rest_framework.settings import api_settings
        from rest_framework.throttling import ScopedRateThrottle
        original = ScopedRateThrottle.THROTTLE_RATES  # read at import time, so override_settings alone does nothing
        ScopedRateThrottle.THROTTLE_RATES = api_settings.DEFAULT_THROTTLE_RATES
        try:
            for _ in range(3):
                self.assertEqual(self.login(password='wrong')['error'], 'invalid_credentials')
            self.assertEqual(self.login()['error'], 'throttled')
        finally:
            ScopedRateThrottle.THROTTLE_RATES = original  # not api_settings: the override is still active here

    # ------------------------------------------------------------------ verification
    def test_resend_verification(self):
        new = User.objects.create_user(email='new@test.com', password=PASSWORD, is_active=False, is_verified=False)
        self.assertEqual(self.post('/account/resend-verification/', {'email': 'new@test.com'})['success'], True)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('https://wallet.example.com' + new.activation_path(), mail.outbox[0].body)
        # Same answer for verified and unknown emails, and no email sent.
        for email in ('alice@test.com', 'nobody@test.com'):
            self.assertEqual(self.post('/account/resend-verification/', {'email': email})['success'], True)
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(ALLOWED_HOSTS=['*'])  # even if ALLOWED_HOSTS is misconfigured
    def test_links_use_site_url_not_the_request_host(self):
        with self.captureOnCommitCallbacks(execute=True):  # the email is queued after the commit
            self.client.post('/account/register/', {'email': 'bob@test.com', 'password': PASSWORD, 'first_name': 'B',
                                                    'last_name': 'B'}, format='json', HTTP_HOST='evil.example.com')
        self.assertIn('https://wallet.example.com/account/activate/', mail.outbox[0].body)
        self.assertNotIn('evil.example.com', mail.outbox[0].body)

    def test_register_rejects_email_differing_only_in_case(self):
        data = self.client.post('/account/register/', {'email': 'ALICE@test.com', 'password': PASSWORD,
                                                       'first_name': 'A', 'last_name': 'A'}, format='json').json()
        self.assertEqual(data['success'], False)

    # ------------------------------------------------------------------ password reset
    def reset_uid_token(self):
        body = mail.outbox[-1].body
        return re.search(r'uid: (\S+)', body).group(1), re.search(r'token: (\S+)', body).group(1)

    def test_forgot_and_reset_password(self):
        old_token = self.login()['token']
        self.assertEqual(self.post('/account/password/forgot/', {'email': 'alice@test.com'})['success'], True)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('https://wallet.example.com/account/password/reset/', mail.outbox[0].body)
        uid, token = self.reset_uid_token()

        weak = self.post('/account/password/reset/', {'uid': uid, 'token': token, 'new_password': '123'})
        self.assertEqual(weak['success'], False)
        data = self.post('/account/password/reset/', {'uid': uid, 'token': token, 'new_password': 'N3w-Passphrase!'})
        self.assertEqual(data['success'], True)
        self.assertFalse(Token.objects.filter(key=old_token).exists())  # signed out everywhere
        self.assertEqual(self.login(password='N3w-Passphrase!')['success'], True)
        # The token works once.
        again = self.post('/account/password/reset/', {'uid': uid, 'token': token, 'new_password': 'An0ther-Pass!'})
        self.assertEqual(again['error'], 'invalid_token')

    @override_settings(PASSWORD_RESET_URL='https://app.example.com/reset?uid={uid}&token={token}')
    def test_reset_link_to_frontend_when_configured(self):
        self.post('/account/password/forgot/', {'email': 'alice@test.com'})
        self.assertRegex(mail.outbox[0].body, r'https://app\.example\.com/reset\?uid=\S+&(amp;)?token=\S+')

    def test_forgot_password_does_not_reveal_accounts(self):
        self.assertEqual(self.post('/account/password/forgot/', {'email': 'nobody@test.com'})['success'], True)
        self.assertEqual(len(mail.outbox), 0)

    def test_reset_with_bad_uid_or_token(self):
        self.assertEqual(self.post('/account/password/reset/', {'uid': 'xx', 'token': 't', 'new_password': 'N3w-Passphrase!'})['error'],
                         'invalid_token')
        self.post('/account/password/forgot/', {'email': 'alice@test.com'})
        uid, _ = self.reset_uid_token()
        self.assertEqual(self.post('/account/password/reset/', {'uid': uid, 'token': 'bad', 'new_password': 'N3w-Passphrase!'})['error'],
                         'invalid_token')

    # ------------------------------------------------------------------ profile
    def test_get_and_update_profile(self):
        token = self.login()['token']
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token}')
        data = self.client.patch('/account/me/', {'first_name': 'Alicia', 'email': 'hijack@test.com',
                                                  'is_verified': False}, format='json').json()
        self.assertEqual((data['first_name'], data['email'], data['is_verified']), ('Alicia', 'alice@test.com', True))
        self.user.refresh_from_db()
        self.assertEqual((self.user.first_name, self.user.email), ('Alicia', 'alice@test.com'))
        self.assertEqual(self.client.put('/account/me/', {'first_name': 'X'}, format='json').json()['success'], False)

    def test_change_password(self):
        token = self.login()['token']
        self.assertEqual(self.post('/account/me/password/', {'old_password': 'wrong', 'new_password': 'N3w-Passphrase!'},
                                   token=token)['error'], 'invalid_credentials')
        data = self.post('/account/me/password/', {'old_password': PASSWORD, 'new_password': 'N3w-Passphrase!'}, token=token)
        self.assertEqual(data['success'], True)
        self.assertNotEqual(data['token'], token)
        self.assertFalse(Token.objects.filter(key=token).exists())
        self.assertEqual(self.login(password='N3w-Passphrase!')['token'], data['token'])


@override_settings(WALLET_ENCRYPTION_KEYS=[TEST_WALLET_KEY])
class AuthHardeningTest(APITestCase):
    def setUp(self):
        cache.clear()

    def test_verification_token_cannot_be_reused_for_another_user(self):
        """User 57's token re-split as user 5's '5' + '7<timestamp>' must not verify user 5."""
        from django.utils.http import int_to_base36

        from account.tokens import EmailVerifyTokenGenerator
        gen = EmailVerifyTokenGenerator()
        victim = User.objects.create_user(email='victim@test.com', password=PASSWORD, is_active=False)
        attacker = User.objects.create_user(email='attacker@test.com', password=PASSWORD, is_active=False)
        User.objects.filter(pk=attacker.pk).update(id=int(f'{victim.pk}7'))
        attacker = User.objects.get(email='attacker@test.com')
        ts_b36, hash_part = gen.make_token(attacker).split('-')
        from django.utils.http import base36_to_int
        forged = f"{int_to_base36(int('7' + str(base36_to_int(ts_b36))))}-{hash_part}"
        self.assertFalse(gen.check_token(victim, forged))
        # and verification tokens are not password reset tokens
        from django.contrib.auth.tokens import default_token_generator
        self.assertFalse(default_token_generator.check_token(victim, gen.make_token(victim)))

    def test_emails_differing_only_in_case_are_one_account(self):
        from django.db import IntegrityError, transaction
        User.objects.create_user(email='Case@test.com', password=PASSWORD)
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user(email='case@test.com', password=PASSWORD)

    @override_settings(REST_FRAMEWORK={
        'EXCEPTION_HANDLER': 'django_central_wallet.utils.custom_exception_handler.custom_exception_handler',
        'DEFAULT_THROTTLE_RATES': {'auth': '100/hour', 'auth_email': '3/hour'}, 'NUM_PROXIES': 0,
    })
    def test_per_email_throttle_survives_ip_rotation(self):
        from rest_framework.settings import api_settings
        from rest_framework.throttling import ScopedRateThrottle

        from account.throttles import EmailRateThrottle
        originals = EmailRateThrottle.THROTTLE_RATES, ScopedRateThrottle.THROTTLE_RATES
        EmailRateThrottle.THROTTLE_RATES = ScopedRateThrottle.THROTTLE_RATES = api_settings.DEFAULT_THROTTLE_RATES
        try:
            User.objects.create_user(email='t@test.com', password=PASSWORD, is_active=True)
            results = [self.client.post('/account/login/', {'email': 't@test.com', 'password': 'x'}, format='json',
                                        HTTP_X_FORWARDED_FOR=f'10.0.0.{i}').json()['error'] for i in range(4)]
            self.assertEqual(results, ['invalid_credentials'] * 3 + ['throttled'])
        finally:
            EmailRateThrottle.THROTTLE_RATES, ScopedRateThrottle.THROTTLE_RATES = originals

    @override_settings(SITE_URL='', DEBUG=False)
    def test_links_refuse_the_host_header_in_production(self):
        from django.core.exceptions import ImproperlyConfigured

        from account.models import site_url
        with self.assertRaises(ImproperlyConfigured):
            site_url('/account/activate/x/y/', 'evil.example.com')

    def test_session_logout_ends_the_session(self):
        user = User.objects.create_user(email='s@test.com', password=PASSWORD, is_active=True)
        self.client.force_login(user)
        self.assertEqual(self.client.get('/account/me/').json()['email'], 's@test.com')
        self.client.post('/account/logout/')
        self.assertEqual(self.client.get('/account/me/').json()['success'], False)


class BlockchainApiPermissionTest(APITestCase):
    """#16: chain settings are admin-only to change and never expose rpc_url."""

    def setUp(self):
        from blockchain.models import Blockchain
        self.chain = Blockchain.objects.create(name='Sepolia', chain_id=11155111, rpc_url='https://secret-rpc.example/key')
        self.user = User.objects.create_user(email='u@test.com', password=PASSWORD, is_active=True)

    def test_users_read_without_rpc_url_and_cannot_write(self):
        self.client.force_authenticate(self.user)
        listing = self.client.get('/blockchain/').json()
        self.assertNotIn('secret-rpc', str(listing))
        self.assertNotIn('secret-rpc', str(self.client.get(f'/blockchain/{self.chain.pk}/').json()))
        self.assertEqual(self.client.post('/blockchain/', {'name': 'X'}, format='json').json()['success'], False)
        self.assertEqual(self.client.patch(f'/blockchain/{self.chain.pk}/', {'rpc_url': 'http://evil'}, format='json')
                         .json()['success'], False)
        self.chain.refresh_from_db()
        self.assertEqual(self.chain.rpc_url, 'https://secret-rpc.example/key')


class ExceptionHandlerTest(APITestCase):
    def test_list_errors_are_wrapped_instead_of_crashing(self):
        from rest_framework import serializers

        from django_central_wallet.utils.custom_exception_handler import custom_exception_handler
        response = custom_exception_handler(serializers.ValidationError(['too short']), {})
        self.assertEqual((response.status_code, response.data['success'], response.data['errors']), (200, False, ['too short']))
