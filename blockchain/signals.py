from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import CoinInfo
from .wallet import backfill_balances


@receiver(post_save, sender=CoinInfo, dispatch_uid='blockchain_backfill_balances')
def backfill_balances_on_coin_info_save(sender, instance: CoinInfo, raw=False, **kwargs):
    if raw:
        return
    backfill_balances(instance)
