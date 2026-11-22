"""Operator alerts: email to ADMINS and/or a Slack-compatible webhook, de-duplicated per key.

Rules
-----
* ``send_alert`` never raises: an alert must not break the job that raised it. Every
  failure (cache, SMTP, webhook) is caught and logged.
* The same key is sent at most once per ``ALERT_DEDUP_SECONDS`` (``cache.add`` is atomic,
  so concurrent workers do not both send). If the cache is unavailable the alert is sent
  anyway: a duplicate is better than a missed alert.
* ``ALERT_WEBHOOK_URL`` is a secret (it authorises posting): it is never logged, and URLs
  are stripped from alert text so RPC API keys in error messages do not leak to chat.
* Every alert is also logged at ERROR level, so it is visible even without a channel.
"""
import json
import logging
import re
import urllib.error
import urllib.request
from typing import Optional

from django.conf import settings
from django.core.cache import cache
from django.core.mail import mail_admins

logger = logging.getLogger(__name__)

CACHE_PREFIX = 'alert:'
MAX_TEXT = 3500  # Slack truncates long messages; keep alerts readable
_URL_RE = re.compile(r'(?i)\b(?:https?|wss?)://\S+')


def redact(text: str) -> str:
    """Replace URLs (they may carry API keys) and cap the length."""
    text = _URL_RE.sub('<url>', str(text))
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT - 3] + '...'


def claim(key: str, seconds: Optional[int] = None) -> bool:
    """True if no alert with this key was claimed within ``seconds`` (and claim it now)."""
    seconds = settings.ALERT_DEDUP_SECONDS if seconds is None else seconds
    if seconds <= 0:
        return True
    try:
        return bool(cache.add(CACHE_PREFIX + key, 1, seconds))
    except Exception as e:
        logger.warning('Alert de-duplication unavailable (%s); sending anyway', type(e).__name__)
        return True


def _post_webhook(url: str, text: str):
    body = json.dumps({'text': text}).encode()
    request = urllib.request.Request(url, data=body, method='POST',
                                     headers={'Content-Type': 'application/json', 'User-Agent': 'central-wallet-alerts'})
    with urllib.request.urlopen(request, timeout=settings.ALERT_WEBHOOK_TIMEOUT) as response:
        response.read(1024)


def _deliver(subject: str, message: str) -> bool:
    delivered = False
    if settings.ADMINS:
        try:
            mail_admins(subject, message, fail_silently=False)
            delivered = True
        except Exception as e:
            logger.error('Alert email failed: %s: %s', type(e).__name__, redact(e))
    url = getattr(settings, 'ALERT_WEBHOOK_URL', '')
    if url:
        try:
            _post_webhook(url, f'{subject}\n{message}')
            delivered = True
        except urllib.error.HTTPError as e:
            logger.error('Alert webhook failed: HTTP %s', e.code)
        except Exception as e:
            # Only the type: some messages (e.g. "unknown url type") contain the URL itself.
            logger.error('Alert webhook failed: %s', type(e).__name__)
    return delivered


def send_alert(key: Optional[str], subject: str, message: str, dedup_seconds: Optional[int] = None) -> bool:
    """Send an alert unless one with the same key was sent recently. Never raises.

    ``key=None`` skips de-duplication (the caller already claimed per-item keys).
    Returns True if it was sent (or at least logged, when no channel is configured).
    """
    try:
        if key is not None and not claim(key, dedup_seconds):
            return False
        subject, message = redact(subject).replace('\n', ' ')[:200], redact(message)
        logger.error('ALERT %s: %s', subject, message)
        _deliver(subject, message)
        return True
    except Exception:  # never let alerting break the caller
        logger.exception('Sending alert %r failed', key)
        return False
