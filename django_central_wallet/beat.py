"""Celery Beat scheduler that touches a heartbeat file on every tick (for the Docker healthcheck).

Beat runs no tasks itself, so ``celery inspect ping`` cannot check it. Its scheduling loop
calls ``tick()`` at least every ``beat_max_interval`` (5 minutes by default; every minute
here, because dispatch_chain_jobs runs every minute), so a fresh file means the loop is
running. Check it without loading Django:

    python -m django_central_wallet.beat      # exit 0 if the heartbeat is fresh, else 1
"""
import logging
import os
import sys
import tempfile
import time

from celery.beat import PersistentScheduler

logger = logging.getLogger(__name__)

HEARTBEAT_FILE = os.getenv('BEAT_HEARTBEAT_FILE') or os.path.join(tempfile.gettempdir(), 'celerybeat-heartbeat')
MAX_AGE = 6 * 60  # seconds: the default max tick interval (5 minutes) plus a margin


class HeartbeatScheduler(PersistentScheduler):
    _heartbeat_error_logged = False

    def tick(self, *args, **kwargs):
        self.write_heartbeat()
        return super().tick(*args, **kwargs)

    def write_heartbeat(self):
        try:
            with open(HEARTBEAT_FILE, 'a'):
                pass
            os.utime(HEARTBEAT_FILE)
        except OSError as e:
            if not self._heartbeat_error_logged:  # never let the healthcheck break scheduling
                logger.warning('Cannot write the beat heartbeat file %s: %s', HEARTBEAT_FILE, e)
                self._heartbeat_error_logged = True


def heartbeat_is_fresh(max_age: float = MAX_AGE) -> bool:
    try:
        return time.time() - os.path.getmtime(HEARTBEAT_FILE) < max_age
    except OSError:
        return False


if __name__ == '__main__':
    sys.exit(0 if heartbeat_is_fresh() else 1)
