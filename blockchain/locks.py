import uuid
from contextlib import contextmanager

from django.core.cache import cache


@contextmanager
def single_run_lock(name: str, timeout: int):
    """Best-effort cross-process lock on the shared cache (Redis in production).

    Yields True when acquired. ``cache.add`` is atomic, so only one worker gets
    the lock; it expires after ``timeout`` seconds in case a worker dies.
    The database constraints remain the real guarantee against double credits.
    """
    key = f'lock:{name}'
    token = uuid.uuid4().hex
    acquired = cache.add(key, token, timeout)
    try:
        yield acquired
    finally:
        if acquired and cache.get(key) == token:
            cache.delete(key)
