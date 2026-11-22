from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from blockchain.key_vault import decrypt_secret, rotate_secret
from blockchain.models import EncryptedPrivateKeyModel


def encrypted_key_models():
    """Every concrete model storing an encrypted private key (user addresses, hot wallets, ...)."""
    from django.apps import apps
    return [m for m in apps.get_models() if issubclass(m, EncryptedPrivateKeyModel)]


class Command(BaseCommand):
    help = ('Re-encrypt all wallet private keys with the first key in WALLET_ENCRYPTION_KEYS. '
            'Only remove an old key from the environment after this succeeds.')

    def handle(self, *args, **options):
        counts = {}
        with transaction.atomic():
            for model in encrypted_key_models():
                count = 0
                for row in model.objects.select_for_update().exclude(encrypted_private_key=''):
                    row.encrypted_private_key = rotate_secret(row.encrypted_private_key)
                    row.save(update_fields=['encrypted_private_key'])
                    count += 1
                counts[model.__name__] = count
            # 2FA secrets are encrypted with the same keys.
            from django.contrib.auth import get_user_model
            User = get_user_model()
            count = 0
            for user in User.objects.select_for_update().exclude(totp_secret=''):
                User.objects.filter(pk=user.pk).update(totp_secret=rotate_secret(user.totp_secret))
                count += 1
            counts['2FA secrets'] = count

        # Verify everything decrypts with the primary key alone before reporting success.
        from django.conf import settings
        from django.test.utils import override_settings
        primary = list(settings.WALLET_ENCRYPTION_KEYS)[:1]
        with override_settings(WALLET_ENCRYPTION_KEYS=primary):
            for model in encrypted_key_models():
                for row in model.objects.exclude(encrypted_private_key=''):
                    try:
                        decrypt_secret(row.encrypted_private_key)
                    except Exception as e:
                        raise CommandError(f'{model.__name__} #{row.pk} does not decrypt with the primary key: {e}')
            for user in User.objects.exclude(totp_secret=''):
                try:
                    decrypt_secret(user.totp_secret)
                except Exception as e:
                    raise CommandError(f'2FA secret of user #{user.pk} does not decrypt with the primary key: {e}')

        summary = ', '.join(f'{name}: {n}' for name, n in counts.items())
        self.stdout.write(self.style.SUCCESS(f'Re-encrypted and verified private keys ({summary}).'))
