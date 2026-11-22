from django.contrib.auth import authenticate
from rest_framework import serializers
from rest_framework.validators import UniqueValidator
from django.utils.translation import gettext_lazy as _

from django_central_wallet.utils.custom_exception_handler import *
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from .models import *
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.contrib.sites.shortcuts import get_current_site

class UserRegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, style={'input_type':'password'})

    current_site_url = serializers.SerializerMethodField()
    
    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'last_name', 'password', 'current_site_url'] 

    def get_current_site_url(self, obj):
        request = self.context.get('request')
        if request is not None:
            current_site = get_current_site(request)
            return current_site.domain if current_site else None
        return None
    
    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise UserAlreadyExistsException
        return value
    
    def validate_password(self, value):
        try:
            validate_password(value)
        except ValidationError as e:
            raise serializers.ValidationError(e.messages)
        return value
    
    def create(self, validated_data):
        user = User.objects.create_user(
            email = validated_data['email'],
            first_name=validated_data['first_name'],
            last_name=validated_data['last_name'],
            password=validated_data['password'],
            is_verified=False,
            is_active=False
        )

        from django.db import transaction

        from .tasks import send_verification_email
        request = self.context.get('request')
        domain = self.get_current_site_url(request)
        transaction.on_commit(lambda: send_verification_email.delay(user.pk, domain))
        return user

class UserSerializer(serializers.ModelSerializer):

    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'last_name', 'is_verified', 'created_at']
        read_only_fields = ['id', 'email', 'is_verified', 'created_at']


def _validate_new_password(value, user=None):
    try:
        validate_password(value, user)
    except ValidationError as e:
        raise serializers.ValidationError({'new_password': e.messages})
    return value


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(style={'input_type': 'password'}, trim_whitespace=False)


class EmailSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(style={'input_type': 'password'}, trim_whitespace=False)


class PasswordChangeSerializer(serializers.Serializer):
    old_password = serializers.CharField(style={'input_type': 'password'}, trim_whitespace=False)
    new_password = serializers.CharField(style={'input_type': 'password'}, trim_whitespace=False)
