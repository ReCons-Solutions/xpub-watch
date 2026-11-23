"""Local password authentication, CSRF protection and login throttling."""
import hmac
import secrets
import threading
import time

from flask import abort, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from db import connect, get_setting, set_setting

PASSWORD_KEY = "auth_password_hash"
MIN_PASSWORD_LENGTH = 10
HASH_METHOD = "pbkdf2:sha256:600000"


def password_configured():
    with connect() as conn:
        return bool(get_setting(conn, PASSWORD_KEY, ""))


def set_password(password):
    validate_new_password(password)
    with connect() as conn:
        set_setting(conn, PASSWORD_KEY, generate_password_hash(password, method=HASH_METHOD))


def verify_password(password):
    with connect() as conn:
        stored = get_setting(conn, PASSWORD_KEY, "")
    if not stored:
        return False
    return check_password_hash(stored, password or "")


def validate_new_password(password):
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise ValueError("Password must have at least %d characters" % MIN_PASSWORD_LENGTH)


def log_in():
    session.clear()
    session.permanent = True
    session["auth"] = True
    session["csrf"] = secrets.token_urlsafe(32)


def logged_in():
    return bool(session.get("auth"))


def csrf_token():
    token = session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf"] = token
    return token


def check_csrf():
    expected = session.get("csrf", "")
    supplied = request.form.get("csrf_token", "") or request.headers.get("X-CSRF-Token", "")
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        abort(400, description="Invalid or missing CSRF token. Reload the page and try again.")


class LoginThrottle(object):
    """Per-client exponential backoff after repeated failed logins."""

    FREE_ATTEMPTS = 5
    MAX_DELAY = 900

    def __init__(self):
        self._lock = threading.Lock()
        self._state = {}  # key -> (failures, locked_until)

    def retry_after(self, key, now=None):
        now = now or time.time()
        with self._lock:
            failures, until = self._state.get(key, (0, 0))
        return max(0, int(until - now + 0.999))

    def failure(self, key, now=None):
        now = now or time.time()
        with self._lock:
            failures, _ = self._state.get(key, (0, 0))
            failures += 1
            until = 0
            if failures >= self.FREE_ATTEMPTS:
                until = now + min(self.MAX_DELAY, 2 ** (failures - self.FREE_ATTEMPTS) * 15)
            self._state[key] = (failures, until)
            if len(self._state) > 10000:
                self._state.clear()

    def success(self, key):
        with self._lock:
            self._state.pop(key, None)


throttle = LoginThrottle()


def client_key():
    # Behind MyNode's nginx every request comes from 127.0.0.1; use the
    # forwarded address only when the request really came through the proxy.
    if request.remote_addr in ("127.0.0.1", "::1"):
        forwarded = request.headers.get("X-XPUBWatch-Client-IP", "").strip()
        if forwarded:
            return forwarded
    return request.remote_addr or "unknown"
