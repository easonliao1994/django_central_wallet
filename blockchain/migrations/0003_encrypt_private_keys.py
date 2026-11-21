from django.db import migrations, models


def encrypt_existing_keys(apps, schema_editor):
    from blockchain.key_vault import encrypt_secret

    CryptoAddress = apps.get_model('blockchain', 'CryptoAddress')
    # Fails with ImproperlyConfigured if rows exist but WALLET_ENCRYPTION_KEYS is not set,
    # so plaintext keys are never silently dropped.
    for row in CryptoAddress.objects.exclude(private_key='').iterator():
        row.encrypted_private_key = encrypt_secret(row.private_key)
        row.save(update_fields=['encrypted_private_key'])


def decrypt_keys(apps, schema_editor):
    from blockchain.key_vault import decrypt_secret

    CryptoAddress = apps.get_model('blockchain', 'CryptoAddress')
    for row in CryptoAddress.objects.exclude(encrypted_private_key='').iterator():
        row.private_key = decrypt_secret(row.encrypted_private_key)
        row.save(update_fields=['private_key'])


class Migration(migrations.Migration):

    dependencies = [
        ('blockchain', '0002_chain_scanner'),
    ]

    operations = [
        migrations.AddField(
            model_name='cryptoaddress',
            name='encrypted_private_key',
            field=models.TextField(default='', editable=False, help_text='Private key encrypted with WALLET_ENCRYPTION_KEYS. Never displayed.'),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='cryptoaddress',
            name='private_key',
            field=models.CharField(default='', max_length=256),
        ),
        migrations.RunPython(encrypt_existing_keys, decrypt_keys),
        migrations.RemoveField(
            model_name='cryptoaddress',
            name='private_key',
        ),
    ]
