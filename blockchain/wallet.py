"""Deposit address generation and balance bookkeeping."""
from typing import Tuple

from django.contrib.auth import get_user_model
from django.db import transaction

from .models import CoinInfo, CoinInfoBalance, CryptoAddress


def generate_keypair() -> Tuple[str, str]:
    """Return a new (checksum address, hex private key) pair using eth_account."""
    from eth_account import Account

    account = Account.create()
    return account.address, account.key.hex()


def active_coin_infos():
    return CoinInfo.objects.filter(blockchain__isnull=False, coin__isnull=False)


def ensure_balances(crypto_address: CryptoAddress) -> None:
    """Make sure the address has a CoinInfoBalance row for every active CoinInfo."""
    CoinInfoBalance.objects.bulk_create(
        [CoinInfoBalance(crypto_address=crypto_address, coin_info=ci) for ci in active_coin_infos()],
        ignore_conflicts=True,
    )


def backfill_balances(coin_info: CoinInfo) -> None:
    """Create a CoinInfoBalance for every existing address when a CoinInfo becomes active."""
    if coin_info.blockchain_id is None or coin_info.coin_id is None:
        return
    CoinInfoBalance.objects.bulk_create(
        [CoinInfoBalance(crypto_address=a, coin_info=coin_info) for a in CryptoAddress.objects.all()],
        ignore_conflicts=True,
    )


def create_deposit_address(user) -> CryptoAddress:
    address, private_key = generate_keypair()
    crypto_address = CryptoAddress(user=user, address=address)
    crypto_address.set_private_key(private_key)
    crypto_address.save()
    ensure_balances(crypto_address)
    return crypto_address


def get_or_create_deposit_address(user) -> CryptoAddress:
    """Return the user's deposit address, creating it (and its balances) if needed.

    The user row is locked only when an address has to be created, so concurrent calls cannot
    create two addresses. It is not locked otherwise: holding it while touching balance rows
    would deadlock with transfers, which lock balance rows and then reference the user.
    """
    existing = CryptoAddress.objects.filter(user=user).order_by('id').first()
    if existing is None:
        with transaction.atomic():
            get_user_model().objects.select_for_update().filter(pk=user.pk).first()
            existing = CryptoAddress.objects.filter(user=user).order_by('id').first()
            if existing is None:
                return create_deposit_address(user)
    ensure_balances(existing)
    return existing
