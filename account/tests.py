from django.test import TestCase, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core import mail
from account.tokens import EmailVerifyTokenGenerator
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
import json

User = get_user_model()

TEST_WALLET_KEY = 'q2zQm1p2kqgk9b6r3XyUoJmYyq1x0n8nqQ3sV5hY6uE='


@override_settings(WALLET_ENCRYPTION_KEYS=[TEST_WALLET_KEY])
class UserTestCase(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(email='user1@test.com', password='password')
        self.user1.is_active = False
        self.user1.save()

    def test_activate_user(self):
        self.user1.activate('email')
        self.assertEqual(self.user1.is_verified, True)
        self.assertEqual(User.objects.count(), 1)

    def test_register_api(self):
        register_api = reverse('register')
        data = {
            "email": 'test@test.com',
            "first_name": "test_first_name",
            "last_name": "test_last_name",
            "password": "testpassword"
        }
        response = self.client.post(register_api, data, format='json')
        response_data = json.loads(response.content)

        self.assertEqual(User.objects.count(), 2)
        self.assertEqual(response_data['email'], data['email'])
        self.assertEqual(response_data['first_name'], data['first_name'])
        self.assertEqual(response_data['last_name'], data['last_name'])

        register_user = User.objects.get(email=data['email'])
        self.assertEqual(register_user.is_active, False)
        self.assertEqual(register_user.is_verified, False)
        self.assertEqual(register_user.check_password(data['password']), True)

        register_user.activate('email')
        self.assertEqual(register_user.is_active, True)
        self.assertEqual(register_user.is_verified, True)

    def test_activate_view(self):
        token_generator = EmailVerifyTokenGenerator()
        token = token_generator.make_token(self.user1)
        uid = urlsafe_base64_encode(force_bytes(self.user1.pk))
        activate_url = reverse('activate', kwargs={'uidb64': uid, 'token': token})

        response = self.client.get(activate_url)
        self.user1.refresh_from_db()
        self.assertEqual(self.user1.is_verified, True)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'mail_verified.html')
    def test_activation_creates_deposit_address_and_balances(self):
        from blockchain.models import Blockchain, Coin, CoinInfo, CoinInfoBalance
        coin = Coin.objects.create(symbol='ETH', name='Ethereum')
        chain = Blockchain.objects.create(name='Ethereum', coin=coin)
        CoinInfo.objects.create(blockchain=chain, coin=coin)

        self.user1.activate('email')
        self.user1.activate('email')  # idempotent

        addresses = self.user1.crypto_addresses.all()
        self.assertEqual(addresses.count(), 1)
        self.assertTrue(addresses[0].address.startswith('0x'))
        self.assertEqual(len(addresses[0].address), 42)
        self.assertEqual(CoinInfoBalance.objects.filter(crypto_address=addresses[0]).count(), 1)
