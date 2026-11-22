from django.contrib.auth.tokens import PasswordResetTokenGenerator


class EmailVerifyTokenGenerator(PasswordResetTokenGenerator):
    """Verification links: bound to the user's id and email, and stop working once verified."""

    key_salt = 'account.tokens.EmailVerifyTokenGenerator'

    def _make_hash_value(self, user, timestamp):
        # The separators matter: without them user 57's "57" + timestamp could be re-split as
        # user 5's "5" + "7<timestamp>", turning one user's link into a valid link for another.
        return f'{user.pk}|{user.email}|{user.is_verified}|{timestamp}'
