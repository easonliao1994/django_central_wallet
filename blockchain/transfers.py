"""Internal (off-chain) transfers between users.

Nothing moves on chain: the sender's ``CoinInfoBalance.internal_amount`` goes down and
the recipient's goes up, in one database transaction. The balance rows involved are
locked in primary-key order, so concurrent transfers in opposite directions cannot
deadlock and a balance can never be spent twice. Each side gets its own TransactionLog
(direction=internal), so the transfer shows up in both users' history.

``internal_amount`` may go negative for the sender (e.g. a user who only received on-chain
deposits); what matters, and what is checked, is the total ``internal + external``.
"""
import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F

from django_central_wallet.utils.custom_exception_handler import (
    DailyLimitExceededException, EmailNotVerifiedException, InsufficientBalanceException, InvalidAmountException,
    RecipientNotFoundException, SelfTransferException, WithdrawalNotAllowedException,
)

from .models import CoinInfo, CoinInfoBalance, CryptoAddress, InternalTransfer, TransactionLog

logger = logging.getLogger(__name__)

MAX_MEMO_LENGTH = 140


def platform_address(address: str):
    """The CryptoAddress for an on-chain address if it belongs to a platform user, else None."""
    from .withdrawals import is_valid_address
    if not isinstance(address, str) or not is_valid_address(address.strip()):
        return None
    return CryptoAddress.objects.filter(address__iexact=address.strip()).select_related('user').first()


def resolve_recipient(to: str):
    """Recipient (user, deposit address) from an email or a platform address."""
    from .wallet import get_or_create_deposit_address

    to = (to or '').strip()
    crypto_address = platform_address(to)
    if crypto_address is not None:
        return crypto_address.user, crypto_address
    if '@' in to:
        user = get_user_model().objects.filter(email__iexact=to, is_active=True, is_verified=True).first()
        if user is not None:
            return user, get_or_create_deposit_address(user)
    raise RecipientNotFoundException()


def _max_balance(user, coin_info) -> int:
    rows = CoinInfoBalance.objects.filter(crypto_address__user=user, coin_info=coin_info) \
        .values_list('internal_amount', 'external_amount')
    return max((int(i) + int(e) for i, e in rows), default=0)


def transfer(sender, coin_info: CoinInfo, to: str, amount: int, memo: str = '') -> InternalTransfer:
    """Move ``amount`` from ``sender`` to the user identified by ``to``.

    Outgoing controls shared with withdrawals: the coin's ``withdrawal_enabled`` switch and the
    daily limit (withdrawals and transfers count together). Transfers are instant, so they are
    not held for review; use the daily limit to bound what a stolen session could move.
    """
    from .withdrawals import _today_total

    if not sender.is_verified:
        raise EmailNotVerifiedException()
    if amount <= 0:
        raise InvalidAmountException()
    if coin_info.coin_id is None or coin_info.blockchain_id is None or not coin_info.withdrawal_enabled:
        raise WithdrawalNotAllowedException()
    # Balance first: an account without funds cannot use transfers to probe which emails exist.
    if _max_balance(sender, coin_info) < amount:
        raise InsufficientBalanceException()
    recipient, recipient_address = resolve_recipient(to)
    if recipient.pk == sender.pk:
        raise SelfTransferException()
    memo = (memo or '').strip()[:MAX_MEMO_LENGTH]

    with transaction.atomic():
        # Serialise this user's outgoing requests so the daily limit cannot be raced past.
        # NO KEY UPDATE: does not block the foreign-key checks other transfers make on this user
        # (a plain FOR UPDATE deadlocks opposite transfers A->B / B->A).
        get_user_model().objects.select_for_update(no_key=True).filter(pk=sender.pk).first()
        limit = int(coin_info.daily_withdrawal_limit or 0)
        if limit > 0 and _today_total(sender, coin_info) + amount > limit:
            raise DailyLimitExceededException()
        recipient_row, _ = CoinInfoBalance.objects.get_or_create(crypto_address=recipient_address, coin_info=coin_info)
        sender_ids = list(CoinInfoBalance.objects.filter(crypto_address__user=sender, coin_info=coin_info)
                          .values_list('pk', flat=True))
        # Lock every row involved, always in primary-key order (no deadlocks between opposite transfers).
        rows = {r.pk: r for r in CoinInfoBalance.objects.select_for_update(of=('self',))
                .filter(pk__in=sorted(set(sender_ids) | {recipient_row.pk}))
                .select_related('crypto_address').order_by('pk')}
        # Older accounts may have several addresses: debit one that holds enough.
        source = next((rows[pk] for pk in sorted(sender_ids) if rows[pk].amount >= amount), None)
        if source is None:
            raise InsufficientBalanceException()

        CoinInfoBalance.objects.filter(pk=source.pk).update(internal_amount=F('internal_amount') - amount)
        CoinInfoBalance.objects.filter(pk=recipient_row.pk).update(internal_amount=F('internal_amount') + amount)

        from_address = source.crypto_address.address.lower()
        to_address = recipient_address.address.lower()
        # Each side's history only shows its own address: the counterparty's deposit address
        # would reveal their whole on-chain deposit history.
        common = dict(blockchain_id=coin_info.blockchain_id, coin_info=coin_info,
                      direction=TransactionLog.Direction.INTERNAL, status=TransactionLog.Status.CONFIRMED, amount=amount)
        sender_log = TransactionLog.objects.create(user=sender, sender_address=from_address, **common)
        recipient_log = TransactionLog.objects.create(user=recipient, recipient_address=to_address, **common)
        t = InternalTransfer.objects.create(
            sender=sender, recipient=recipient, coin_info=coin_info, amount=amount,
            sender_address=from_address, recipient_address=to_address, recipient_input=(to or '').strip()[:256],
            memo=memo, sender_log=sender_log, recipient_log=recipient_log)
    logger.info('Internal transfer #%s: %s %s from user %s to user %s', t.pk, amount, coin_info, sender.pk, recipient.pk)
    return t
