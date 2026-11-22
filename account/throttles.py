from rest_framework.throttling import SimpleRateThrottle


class EmailRateThrottle(SimpleRateThrottle):
    """Limits attempts per target email (on top of the per-IP 'auth' throttle), so rotating IPs
    does not allow unlimited password guessing against one account."""
    scope = 'auth_email'

    def get_cache_key(self, request, view):
        email = request.data.get('email') if hasattr(request, 'data') else None
        if not isinstance(email, str) or not email.strip():
            return None
        return self.cache_format % {'scope': self.scope, 'ident': email.strip().lower()}
