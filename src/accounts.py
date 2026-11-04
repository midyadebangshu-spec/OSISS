"""Scholar accounts: registration, password hashing, login sessions. Model- and web-framework-free.

Passwords are hashed with scrypt (standard library, no extra dependency). A session is a random token held in an
HttpOnly cookie; only its SHA-256 is stored, so a database leak does not hand out logins. Functions take an open
psycopg2 connection and commit nothing (the caller owns the transaction), except where noted.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from typing import Any, Dict, List, Optional, Tuple

SESSION_DAYS = 30
MIN_PASSWORD = 8
MAX_PASSWORD = 200
MAX_FAILURES = 8  # wrong passwords per address+email within FAILURE_WINDOW before logins are refused
FAILURE_WINDOW = 15 * 60
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")


class AccountError(Exception):
    """A request the user can fix; `status` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# --- passwords ----------------------------------------------------------------------------------------------------


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(expected) // 2
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), expected)


def check_password_rules(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise AccountError(f"Password must have at least {MIN_PASSWORD} characters.")
    if len(password) > MAX_PASSWORD:
        raise AccountError(f"Password must have at most {MAX_PASSWORD} characters.")


def normalize_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise AccountError("Enter a valid email address.")
    return email


def clean_name(name: Optional[str]) -> str:
    return " ".join((name or "").split())[:100]


# --- users --------------------------------------------------------------------------------------------------------


def public_user(row: Tuple) -> Dict[str, Any]:
    """Row of (id, email, name, must_change_password, created_at) as the dict the API returns."""
    return {
        "id": row[0],
        "email": row[1],
        "name": row[2] or "",
        "must_change_password": bool(row[3]),
        "created_at": row[4].isoformat() if row[4] else None,
    }


USER_COLUMNS = "id, email, name, must_change_password, created_at"


def create_user(
    connection, email: str, password: str, name: Optional[str] = None, must_change_password: bool = False
) -> Dict[str, Any]:
    email = normalize_email(email)
    check_password_rules(password)
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM users WHERE email = %s", (email,))
        if cursor.fetchone():
            raise AccountError("An account with this email already exists.", 409)
        cursor.execute(
            f"INSERT INTO users (email, name, password_hash, must_change_password) VALUES (%s, %s, %s, %s) "
            f"RETURNING {USER_COLUMNS}",
            (email, clean_name(name), hash_password(password), must_change_password),
        )
        return public_user(cursor.fetchone())


def get_user(connection, user_id: int) -> Optional[Dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {USER_COLUMNS} FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
    return public_user(row) if row else None


# --- failed logins ------------------------------------------------------------------------------------------------


def _failure_key(address: str, email: str) -> str:
    return f"{address}|{email}"


def login_blocked(connection, address: str, email: str, now: Optional[float] = None) -> float:
    """Seconds until another login attempt is allowed (0 when allowed)."""
    now = time.time() if now is None else now
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*), MIN(at) FROM user_login_failures WHERE key = %s AND at > %s",
            (_failure_key(address, email), now - FAILURE_WINDOW),
        )
        count, oldest = cursor.fetchone()
    if count >= MAX_FAILURES and oldest is not None:
        return max(0.0, oldest + FAILURE_WINDOW - now)
    return 0.0


def record_failure(connection, address: str, email: str, now: Optional[float] = None) -> None:
    now = time.time() if now is None else now
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM user_login_failures WHERE at < %s", (now - FAILURE_WINDOW,))
        cursor.execute(
            "INSERT INTO user_login_failures (key, at) VALUES (%s, %s)", (_failure_key(address, email), now)
        )


def clear_failures(connection, address: str, email: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM user_login_failures WHERE key = %s", (_failure_key(address, email),))


# --- sessions -----------------------------------------------------------------------------------------------------


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# Verified against when the email is unknown, so a wrong email takes as long as a wrong password.
_DUMMY_HASH = hash_password("not-a-real-password", salt=b"\0" * 16)


def login(connection, email: str, password: str, address: str) -> Tuple[Dict[str, Any], str]:
    """Check the credentials; returns (user, session token). Failed attempts are counted and committed."""
    try:
        email = normalize_email(email)
    except AccountError:
        raise AccountError("Wrong email or password.", 401) from None
    wait = login_blocked(connection, address, email)
    if wait > 0:
        raise AccountError(f"Too many wrong passwords; try again in {int(wait // 60) + 1} minutes.", 429)
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {USER_COLUMNS}, password_hash, disabled FROM users WHERE email = %s", (email,)
        )
        row = cursor.fetchone()
    valid = verify_password(password, row[5] if row else _DUMMY_HASH)
    if not row or not valid:
        record_failure(connection, address, email)
        connection.commit()
        raise AccountError("Wrong email or password.", 401)
    if row[6]:
        raise AccountError("This account is disabled. Ask the administrator.", 403)
    clear_failures(connection, address, email)
    user = public_user(row[:5])
    token = start_session(connection, user["id"])
    with connection.cursor() as cursor:
        cursor.execute("UPDATE users SET last_login_at = NOW() WHERE id = %s", (user["id"],))
    return user, token


def start_session(connection, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM user_sessions WHERE expires_at < NOW()")
        cursor.execute(
            "INSERT INTO user_sessions (token_hash, user_id, expires_at) VALUES (%s, %s, NOW() + %s * INTERVAL '1 day')",
            (_token_hash(token), user_id, SESSION_DAYS),
        )
    return token


def user_for_token(connection, token: Optional[str]) -> Optional[Dict[str, Any]]:
    """The user a valid, unexpired session belongs to (None for a missing/expired token or a disabled user)."""
    if not token:
        return None
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT u.id, u.email, u.name, u.must_change_password, u.created_at FROM user_sessions s "
            "JOIN users u ON u.id = s.user_id WHERE s.token_hash = %s AND s.expires_at > NOW() AND NOT u.disabled",
            (_token_hash(token),),
        )
        row = cursor.fetchone()
    return public_user(row) if row else None


def end_session(connection, token: Optional[str]) -> None:
    if token:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM user_sessions WHERE token_hash = %s", (_token_hash(token),))


def change_password(connection, user_id: int, current: str, new: str, keep_token: Optional[str] = None) -> None:
    """The scholar changes their own password; every other login of theirs is signed out."""
    check_password_rules(new)
    with connection.cursor() as cursor:
        cursor.execute("SELECT password_hash FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
    if not row or not verify_password(current, row[0]):
        raise AccountError("The current password is wrong.", 403)
    _set_password(connection, user_id, new, must_change=False, keep_token=keep_token)


def _set_password(connection, user_id: int, password: str, must_change: bool, keep_token: Optional[str] = None) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE users SET password_hash = %s, must_change_password = %s WHERE id = %s",
            (hash_password(password), must_change, user_id),
        )
        if keep_token:
            cursor.execute(
                "DELETE FROM user_sessions WHERE user_id = %s AND token_hash <> %s", (user_id, _token_hash(keep_token))
            )
        else:
            cursor.execute("DELETE FROM user_sessions WHERE user_id = %s", (user_id,))


# --- administrator ------------------------------------------------------------------------------------------------


def generate_temporary_password() -> str:
    return secrets.token_urlsafe(9)


def admin_reset_password(connection, user_id: int) -> str:
    """Set a temporary password the scholar must replace at the next login; returns it (shown once to the admin)."""
    if not get_user(connection, user_id):
        raise AccountError("Unknown user.", 404)
    temporary = generate_temporary_password()
    _set_password(connection, user_id, temporary, must_change=True)
    return temporary


def list_users(connection) -> List[Dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT u.id, u.email, u.name, u.must_change_password, u.created_at, u.disabled, u.last_login_at, "
            "(SELECT COUNT(*) FROM evidence_items e WHERE e.owner_id = u.id), "
            "(SELECT COUNT(*) FROM notes n WHERE n.owner_id = u.id) "
            "FROM users u ORDER BY u.created_at DESC, u.id DESC"
        )
        rows = cursor.fetchall()
    return [
        {
            **public_user(row[:5]),
            "disabled": row[5],
            "last_login_at": row[6].isoformat() if row[6] else None,
            "evidence_count": row[7],
            "note_count": row[8],
        }
        for row in rows
    ]


def set_disabled(connection, user_id: int, disabled: bool) -> None:
    with connection.cursor() as cursor:
        cursor.execute("UPDATE users SET disabled = %s WHERE id = %s", (disabled, user_id))
        if not cursor.rowcount:
            raise AccountError("Unknown user.", 404)
        if disabled:
            cursor.execute("DELETE FROM user_sessions WHERE user_id = %s", (user_id,))


def delete_user(connection, user_id: int) -> None:
    """Removes the account and, by cascade, everything the scholar saved."""
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
        if not cursor.rowcount:
            raise AccountError("Unknown user.", 404)
