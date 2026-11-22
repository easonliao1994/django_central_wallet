"""GET /health/: is the app able to reach its database and Redis?

200 {"status": "ok", ...} or 503 {"status": "failing", ...} with ok/failing per component and
nothing else (no versions, hosts or error messages), no authentication.

A plain Django view on purpose: the project's DRF exception handler reports errors with
HTTP 200, which a health probe would read as healthy.

``HealthCheckMiddleware`` is the first middleware and answers GET/HEAD /health/ itself,
before ``SecurityMiddleware`` (HTTPS redirect) and ``CommonMiddleware`` (ALLOWED_HOSTS
check). A probe from inside the container (``http://127.0.0.1:8000/health/``) or from a
load balancer using the instance IP therefore gets a real answer instead of a 400
(host not allowed) or 301 (redirect to HTTPS), whatever the production settings are.
Skipping those checks is safe here: the response is fixed JSON that never contains a URL
or anything derived from the Host header, and it sets no cookies.
"""
from django.db import connection
from django.http import JsonResponse
from django.utils.cache import add_never_cache_headers
from django.views.decorators.http import require_safe

HEALTH_PATH = '/health/'
REDIS_TIMEOUT = 3  # seconds


def check_database() -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            return cursor.fetchone()[0] == 1
    except Exception:
        return False


def check_redis() -> bool:
    from django.conf import settings
    try:
        import redis
        client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=REDIS_TIMEOUT,
                                      socket_timeout=REDIS_TIMEOUT)
        try:
            return bool(client.ping())
        finally:
            client.close()
    except Exception:
        return False


def health_response() -> JsonResponse:
    checks = {'database': check_database(), 'redis': check_redis()}
    healthy = all(checks.values())
    response = JsonResponse(
        {'status': 'ok' if healthy else 'failing',
         'checks': {name: 'ok' if ok else 'failing' for name, ok in checks.items()}},
        status=200 if healthy else 503)
    add_never_cache_headers(response)
    # Django logs 5xx responses on django.request at ERROR, which emails ADMINS when DEBUG=False:
    # one email per probe (every 30 seconds) during an outage. Probes report the outage instead.
    response._has_been_logged = True
    return response


@require_safe
def health(request):
    return health_response()


class HealthCheckMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path_info == HEALTH_PATH and request.method in ('GET', 'HEAD'):
            return health_response()
        return self.get_response(request)
