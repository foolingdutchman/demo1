import hashlib
import hmac
import re
import secrets
from http.cookies import CookieError, SimpleCookie
import time

import db_manager


PASSWORD_ITERATIONS = 600_000
SESSION_LIFETIME = 7 * 24 * 60 * 60


class AuthError(Exception):
    def __init__(self, status, code, message_key):
        super().__init__(code)
        self.status = status
        self.code = code
        self.message_key = message_key


def _session_token_hash(token):
    try:
        return hashlib.sha256(token.encode("ascii")).hexdigest()
    except UnicodeEncodeError:
        return None


def get_current_user(cookie_header):
    cookies = SimpleCookie()
    try:
        cookies.load(cookie_header)
    except CookieError:
        return None
    session_cookie = cookies.get("session")
    if session_cookie is None:
        return None
    token_hash = _session_token_hash(session_cookie.value)
    if token_hash is None:
        return None
    return db_manager.get_current_user(token_hash, int(time.time()))


def _create_session(user_id):
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    now = int(time.time())
    expires_at = now + SESSION_LIFETIME
    db_manager.create_session(token_hash, user_id, expires_at, now)
    return (
        f"session={token}; HttpOnly; SameSite=Lax; Path=/; "
        f"Max-Age={SESSION_LIFETIME}"
    )


def authenticate(fields):
    mode = fields.get("mode", "login")
    if mode not in {"login", "register"}:
        raise AuthError(400, "invalid_mode", "request.invalid_mode")
    username = fields.get("username", "").strip()
    password = fields.get("password", "")
    confirm_password = fields.get("confirm_password", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
        raise AuthError(400, "invalid_username", "auth.invalid_username")
    if not 8 <= len(password) <= 128:
        raise AuthError(400, "invalid_password", "auth.invalid_password_length")

    user = db_manager.get_user_auth_record(username)
    if mode == "register":
        if user is not None:
            raise AuthError(409, "username_exists", "auth.username_exists")
        if password != confirm_password:
            raise AuthError(400, "password_mismatch", "auth.password_mismatch")
        if not all(
            re.search(pattern, password)
            for pattern in (r"[A-Z]", r"[a-z]", r"[0-9]")
        ):
            raise AuthError(400, "weak_password", "auth.password_strength")
        salt = secrets.token_bytes(16)
        password_hash = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
        )
        user_id = db_manager.create_user(username, salt, password_hash)
    else:
        if user is None:
            raise AuthError(404, "username_missing", "auth.username_missing")
        supplied_hash = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), user[2], PASSWORD_ITERATIONS
        )
        if not hmac.compare_digest(supplied_hash, user[3]):
            raise AuthError(401, "invalid_credentials", "auth.invalid_credentials")
        user_id = user[0]
        username = user[1]

    return (
        {"user": {"id": user_id, "username": username}},
        200 if mode == "login" else 201,
        _create_session(user_id),
    )


def logout(cookie_header):
    cookies = SimpleCookie()
    try:
        cookies.load(cookie_header)
    except CookieError:
        cookies = SimpleCookie()
    session_cookie = cookies.get("session")
    if session_cookie is not None:
        token_hash = _session_token_hash(session_cookie.value)
        if token_hash is not None:
            db_manager.delete_session(token_hash)
    return "session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"
