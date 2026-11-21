from django.core.management.base import BaseCommand
from django.db import transaction

from blockchain.key_vault import rotate_secret
from blockchain.models import CryptoAddress


class Command(BaseCommand):
    help = 'Re-encrypt all wallet private keys with the first key in WALLET_ENCRYPTION_KEYS.'

    def handle(self, *args, **options):
        count = 0
        with transaction.atomic():
            for row in CryptoAddress.objects.select_for_update().exclude(encrypted_private_key=''):
                row.encrypted_private_key = rotate_secret(row.encrypted_private_key)
                row.save(update_fields=['encrypted_private_key'])
                count += 1
        self.stdout.write(self.style.SUCCESS(f'Re-encrypted {count} private key(s).'))
