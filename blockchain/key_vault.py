"""Encryption of wallet private keys at rest.

Keys are encrypted with Fernet (AES-128-CBC + HMAC-SHA256). The encryption keys
come from ``settings.WALLET_ENCRYPTION_KEYS`` (comma separated in the
``WALLET_ENCRYPTION_KEYS`` environment variable) and are never stored in the
database or the repository.

Key rotation: put the new key first and keep the old ones after it. Decryption
tries every key; encryption always uses the first one. Run
``python manage.py rotate_wallet_keys`` to re-encrypt everything with the new
key, then remove the old key from the environment.

Generate a key with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _fernet() -> MultiFernet:
    keys = getattr(settings, 'WALLET_ENCRYPTION_KEYS', None) or []
    if isinstance(keys, str):
        keys = [k.strip() for k in keys.split(',') if k.strip()]
    if not keys:
        raise ImproperlyConfigured(
            'WALLET_ENCRYPTION_KEYS is not set. Generate one with '
            '`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` '
            'and add it to .env')
    try:
        return MultiFernet([Fernet(k) for k in keys])
    except (ValueError, TypeError) as e:
        raise ImproperlyConfigured(f'Invalid WALLET_ENCRYPTION_KEYS: {e}') from e


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise ImproperlyConfigured('Cannot decrypt wallet key: no configured WALLET_ENCRYPTION_KEYS matches') from e


def rotate_secret(token: str) -> str:
    """Re-encrypt a token with the primary (first) key."""
    return _fernet().rotate(token.encode()).decode()
