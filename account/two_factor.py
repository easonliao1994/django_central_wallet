"""Two-factor authentication (TOTP) and the security checks for outgoing funds.

Withdrawals and internal transfers go through :func:`authorize_outgoing`, which enforces:

* the cooldown after a password reset or after 2FA is disabled (``withdrawals_locked_until``);
* a valid TOTP code when 2FA is on, and 2FA itself when ``REQUIRE_2FA_FOR_WITHDRAWAL`` is set;
* the withdrawal address whitelist, when the user turned it on.

Codes are single use: the time step of the last accepted code is stored on the user, and a code
from the same or an earlier step is rejected. Wrong codes are counted per user in the cache, and
after ``OTP_MAX_FAILURES`` within ``OTP_FAILURE_WINDOW`` seconds every code is refused until the
window passes, so the 6 digits cannot be brute forced.
"""
import hmac
import time
from datetime import timedelta

import pyotp
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from blockchain.key_vault import decrypt_secret, encrypt_secret
from django_central_wallet.utils.custom_exception_handler import (
    AddressNotWhitelistedException, InvalidOTPException, OTPRequiredException, TooManyOTPAttemptsException,
    TwoFactorRequiredException, WithdrawalsLockedException,
)

STEP_SECONDS = 30
VALID_WINDOW = 1  # also accept the previous and the next 30 s step (clock drift)


def _clock() -> float:
    return time.time()


def _setting(name, default):
    return getattr(settings, name, default)


def require_2fa_for_withdrawal() -> bool:
    return bool(_setting('REQUIRE_2FA_FOR_WITHDRAWAL', True))


def address_cooldown() -> timedelta:
    return timedelta(hours=float(_setting('WITHDRAWAL_ADDRESS_COOLDOWN_HOURS', 24)))


def security_cooldown() -> timedelta:
    return timedelta(hours=float(_setting('SECURITY_CHANGE_COOLDOWN_HOURS', 24)))


# --------------------------------------------------------------------------- TOTP
def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(user, secret: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=_setting('TOTP_ISSUER', 'DjangoCentralWallet'))


def _failure_key(user) -> str:
    return f'otp-failures:{user.pk}'


def _register_failure(user):
    key = _failure_key(user)
    window = int(_setting('OTP_FAILURE_WINDOW', 900))
    if cache.add(key, 1, window):
        return
    try:
        cache.incr(key)
    except ValueError:  # expired between add() and incr()
        cache.add(key, 1, window)


def _locked_out(user) -> bool:
    return (cache.get(_failure_key(user)) or 0) >= int(_setting('OTP_MAX_FAILURES', 5))


def _matching_step(secret: str, code: str, now: float = None):
    """The time step whose code equals ``code`` (within the drift window), or None."""
    code = (code or '').strip().replace(' ', '')
    if len(code) != 6 or not code.isdigit():
        return None
    totp = pyotp.TOTP(secret)
    now = _clock() if now is None else now
    current = int(now // STEP_SECONDS)
    for step in range(current - VALID_WINDOW, current + VALID_WINDOW + 1):
        if hmac.compare_digest(totp.at(step * STEP_SECONDS), code):
            return step
    return None


def check_code(user, code: str) -> None:
    """Accept ``code`` once for ``user`` or raise. Locks the user row (FOR NO KEY UPDATE, like transfers)
    and records the step, so it must run inside the caller's transaction: if the withdrawal then fails,
    the code is not used up."""
    User = get_user_model()
    if not (code or '').strip():
        raise OTPRequiredException()
    if _locked_out(user):
        raise TooManyOTPAttemptsException()
    with transaction.atomic():
        row = User.objects.select_for_update(no_key=True).only(
            'pk', 'totp_secret', 'totp_last_step').get(pk=user.pk)
        secret = decrypt_secret(row.totp_secret)
        step = _matching_step(secret, code)
        if step is None or (row.totp_last_step is not None and step <= row.totp_last_step):
            _register_failure(user)
            raise InvalidOTPException()
        User.objects.filter(pk=user.pk).update(totp_last_step=step)
        user.totp_last_step = step


def start_setup(user) -> tuple:
    """Store a new pending secret (encrypted) and return ``(secret, otpauth_uri)``."""
    secret = new_secret()
    user.totp_secret = encrypt_secret(secret)
    user.totp_enabled_at = None
    user.totp_last_step = None
    user.save(update_fields=['totp_secret', 'totp_enabled_at', 'totp_last_step'])
    return secret, provisioning_uri(user, secret)


def confirm_setup(user, code: str) -> None:
    if not user.totp_secret:
        raise OTPRequiredException()
    with transaction.atomic():
        check_code(user, code)
        user.totp_enabled_at = timezone.now()
        user.save(update_fields=['totp_enabled_at'])


def disable(user, code: str) -> None:
    with transaction.atomic():
        check_code(user, code)
        user.totp_secret = ''
        user.totp_enabled_at = None
        user.totp_last_step = None
        lock_withdrawals(user, save=False)
        user.save(update_fields=['totp_secret', 'totp_enabled_at', 'totp_last_step', 'withdrawals_locked_until'])


def lock_withdrawals(user, save=True) -> None:
    """Block withdrawals and transfers for the security cooldown (after a password reset, 2FA disabled)."""
    until = timezone.now() + security_cooldown()
    if user.withdrawals_locked_until is None or user.withdrawals_locked_until < until:
        user.withdrawals_locked_until = until
        if save:
            user.save(update_fields=['withdrawals_locked_until'])


# --------------------------------------------------------------------------- whitelist
def whitelist_active(user, now=None) -> bool:
    """Whitelist mode is on, and a requested switch-off has not taken effect yet."""
    if not user.withdrawal_whitelist_enabled:
        return False
    if user.whitelist_disable_requested_at is None:
        return True
    return (now or timezone.now()) < user.whitelist_disable_requested_at + address_cooldown()


def whitelisted_addresses(user, now=None) -> set:
    """Lower-cased addresses the user may currently withdraw to (cooldown passed)."""
    from .models import WithdrawalAddress
    return {a.lower() for a in WithdrawalAddress.objects.filter(
        user=user, active_from__lte=now or timezone.now()).values_list('address', flat=True)}


# --------------------------------------------------------------------------- outgoing funds
def authorize_outgoing(user, otp_code: str = None, to_address: str = None):
    """Run the security checks for a withdrawal (``to_address`` given) or a transfer.

    Must be called inside the transaction that creates the withdrawal or transfer. Returns the set of
    whitelisted addresses when whitelist mode is active (transfers check the resolved recipient
    against it), else None.
    """
    now = timezone.now()
    if user.withdrawals_locked_until and user.withdrawals_locked_until > now:
        raise WithdrawalsLockedException()
    if user.two_factor_enabled:
        check_code(user, otp_code)
    elif require_2fa_for_withdrawal():
        raise TwoFactorRequiredException()

    if not whitelist_active(user, now):
        return None
    allowed = whitelisted_addresses(user, now)
    if to_address is not None and to_address.strip().lower() not in allowed:
        raise AddressNotWhitelistedException()
    return allowed
