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
        if User.objects.filter(email=value).exists():
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

        request = self.context.get('request')
        current_site_url = self.get_current_site_url(request)
        user.send_verification_mail(current_site_url)
        return user

class UserSerializer(serializers.ModelSerializer):
    
    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'last_name', 'is_verified', 'created_at']
