import json

from django.urls import reverse
from rest_framework.test import APITestCase
from django.contrib.auth.models import User
from rest_framework import status
from .models import *

class UserAPITest(APITestCase):
    def setUp(self):
        self.user1 = User.objects.create_user('user1@test.com', 'Test123a')
        self.assertEqual(self.user1.is_verified, False)
        self.user1.activate('email')
        self.assertEqual(self.user1.is_verified, True)
        
        self.assertEqual(User.objects.count(), 1)
    
    def test_register_api(self):
        # url setup
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