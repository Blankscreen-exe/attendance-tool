"""Sign-in lockout after repeated wrong passwords.

It works on the username that was typed, whether or not such an account
exists, so the lock gives nothing away about which usernames are real. It
hooks into Django's authentication itself, so every way of signing in is
covered, including /admin/.
"""

from datetime import timedelta

from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.dispatch import receiver
from django.utils import timezone

from .models import LoginThrottle

MAX_ATTEMPTS = 5
WINDOW = timedelta(minutes=15)  # the wrong passwords must fall within this
LOCK_FOR = timedelta(minutes=15)
FORGET_AFTER = timedelta(days=1)


def _key(username):
    return (username or "").strip().lower()[:150]


def locked_until(username):
    """When the lock on a username ends, or None if it is not locked."""
    key = _key(username)
    if not key:
        return None
    return (
        LoginThrottle.objects.filter(username=key, locked_until__gt=timezone.now())
        .values_list("locked_until", flat=True)
        .first()
    )


def locked_usernames():
    return dict(
        LoginThrottle.objects.filter(locked_until__gt=timezone.now()).values_list("username", "locked_until")
    )


@transaction.atomic
def record_failure(username):
    key = _key(username)
    if not key:
        return
    now = timezone.now()
    throttle, _ = LoginThrottle.objects.select_for_update().get_or_create(
        username=key, defaults={"window_start": now}
    )
    if throttle.locked_until and throttle.locked_until > now:
        return
    if now - throttle.window_start > WINDOW:
        throttle.failures = 0
        throttle.window_start = now
    throttle.failures += 1
    if throttle.failures >= MAX_ATTEMPTS:
        throttle.failures = 0
        throttle.window_start = now
        throttle.locked_until = now + LOCK_FOR
    throttle.save()
    # Typed-in usernames that never came back would otherwise pile up.
    LoginThrottle.objects.filter(window_start__lt=now - FORGET_AFTER).exclude(locked_until__gt=now).delete()


def clear(username):
    LoginThrottle.objects.filter(username=_key(username)).delete()


class LockoutBackend:
    """Listed first in AUTHENTICATION_BACKENDS: refuses a locked username before the password is checked.

    It only ever says no. It deliberately has no `get_user`, so sessions are
    always tied to the real backend that checked the password.
    """

    def authenticate(self, request, username=None, **credentials):
        if locked_until(username):
            raise PermissionDenied
        return None


@receiver(user_login_failed)
def _count_failure(sender, credentials, **kwargs):
    record_failure(credentials.get("username"))


@receiver(user_logged_in)
def _forget_failures(sender, user, **kwargs):
    clear(user.get_username())
