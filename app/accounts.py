"""User accounts, sessions, login throttling and AI-usage accounting.

This database (auth.db) never contains card/contact data. Each user's cards live
in their own SQLite file managed by app.store.UserStore.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCRYPT_N, SCRYPT_R, SCRYPT_P, SCRYPT_LEN = 2**14, 8, 1, 32

# Login throttling: failures allowed per window.
FAIL_WINDOW_S = 15 * 60
MAX_FAILS_PER_USERNAME = 10
MAX_FAILS_PER_IP = 30


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_LEN)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_hex, digest_hex = stored.split("$")
        if algo != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p),
            dklen=len(digest_hex) // 2,
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


# A valid hash for timing equalisation when the username does not exist.
_DUMMY_HASH = hash_password(secrets.token_hex(8))


def _now() -> float:
    return time.time()


def _iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else _now(), tz=timezone.utc).isoformat(timespec="seconds")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class User:
    id: str
    username: str
    is_admin: bool
    disabled: bool
    created_at: str

    def public(self) -> dict:
        return {"id": self.id, "username": self.username, "is_admin": self.is_admin,
                "disabled": self.disabled, "created_at": self.created_at}


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    pw_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS login_failures (
    key TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS login_failures_key ON login_failures(key, ts);
CREATE TABLE IF NOT EXISTS ai_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL
);
CREATE INDEX IF NOT EXISTS ai_usage_user_ts ON ai_usage(user_id, ts);
"""


class AccountStore:
    def __init__(self, db_path: Path, session_days: int = 30):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_seconds = session_days * 86400
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _run(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    rows = conn.execute(sql, params).fetchall()
                return rows
            finally:
                conn.close()

    @staticmethod
    def _user(row: sqlite3.Row | None) -> User | None:
        if row is None:
            return None
        return User(id=row["id"], username=row["username"], is_admin=bool(row["is_admin"]),
                    disabled=bool(row["disabled"]), created_at=row["created_at"])

    # ---- users -----------------------------------------------------------
    def create_user(self, username: str, password: str, is_admin: bool = False) -> User:
        uid = uuid.uuid4().hex
        try:
            self._run(
                "INSERT INTO users (id, username, pw_hash, is_admin, disabled, created_at) VALUES (?,?,?,?,0,?)",
                (uid, username.strip(), hash_password(password), int(is_admin), _iso()),
            )
        except sqlite3.IntegrityError as e:
            raise ValueError("username already exists") from e
        user = self.get_user(uid)
        assert user is not None
        return user

    def get_user(self, user_id: str) -> User | None:
        rows = self._run("SELECT * FROM users WHERE id = ?", (user_id,))
        return self._user(rows[0] if rows else None)

    def list_users(self) -> list[User]:
        return [self._user(r) for r in self._run("SELECT * FROM users ORDER BY username COLLATE NOCASE")]  # type: ignore[misc]

    def count_users(self) -> int:
        return self._run("SELECT COUNT(*) AS n FROM users")[0]["n"]

    def update_user(self, user_id: str, *, disabled: bool | None = None, is_admin: bool | None = None,
                    password: str | None = None) -> User | None:
        if self.get_user(user_id) is None:
            return None
        if disabled is not None:
            self._run("UPDATE users SET disabled = ? WHERE id = ?", (int(disabled), user_id))
            if disabled:
                self.revoke_sessions(user_id)
        if is_admin is not None:
            self._run("UPDATE users SET is_admin = ? WHERE id = ?", (int(is_admin), user_id))
        if password is not None:
            self._run("UPDATE users SET pw_hash = ? WHERE id = ?", (hash_password(password), user_id))
            self.revoke_sessions(user_id)
        return self.get_user(user_id)

    def delete_user(self, user_id: str) -> bool:
        self.revoke_sessions(user_id)
        rows = self._run("DELETE FROM users WHERE id = ? RETURNING id", (user_id,))
        self._run("DELETE FROM ai_usage WHERE user_id = ?", (user_id,))
        return bool(rows)

    def authenticate(self, username: str, password: str) -> User | None:
        rows = self._run("SELECT * FROM users WHERE username = ?", (username.strip(),))
        if not rows:
            verify_password(password, _DUMMY_HASH)  # equalise timing
            return None
        row = rows[0]
        if not verify_password(password, row["pw_hash"]) or row["disabled"]:
            return None
        return self._user(row)

    def check_password(self, user_id: str, password: str) -> bool:
        rows = self._run("SELECT pw_hash FROM users WHERE id = ?", (user_id,))
        return bool(rows) and verify_password(password, rows[0]["pw_hash"])

    # ---- sessions --------------------------------------------------------
    def create_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        now = _now()
        self._run("DELETE FROM sessions WHERE expires_at < ?", (now,))
        self._run(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (_token_hash(token), user_id, now, now + self.session_seconds),
        )
        return token

    def user_for_session(self, token: str | None) -> User | None:
        if not token or len(token) > 200:
            return None
        rows = self._run(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ? AND s.expires_at > ? AND u.disabled = 0",
            (_token_hash(token), _now()),
        )
        return self._user(rows[0] if rows else None)

    def delete_session(self, token: str | None) -> None:
        if token:
            self._run("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))

    def revoke_sessions(self, user_id: str, except_token: str | None = None) -> None:
        if except_token:
            self._run("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (user_id, _token_hash(except_token)))
        else:
            self._run("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    # ---- login throttling ------------------------------------------------
    def login_blocked(self, username: str, ip: str) -> bool:
        since = _now() - FAIL_WINDOW_S
        self._run("DELETE FROM login_failures WHERE ts < ?", (since,))
        n_user = self._run("SELECT COUNT(*) AS n FROM login_failures WHERE key = ?", ("u:" + username.lower(),))[0]["n"]
        n_ip = self._run("SELECT COUNT(*) AS n FROM login_failures WHERE key = ?", ("ip:" + ip,))[0]["n"]
        return n_user >= MAX_FAILS_PER_USERNAME or n_ip >= MAX_FAILS_PER_IP

    def record_login_failure(self, username: str, ip: str) -> None:
        now = _now()
        self._run("INSERT INTO login_failures (key, ts) VALUES (?, ?)", ("u:" + username.lower(), now))
        self._run("INSERT INTO login_failures (key, ts) VALUES (?, ?)", ("ip:" + ip, now))

    def clear_login_failures(self, username: str) -> None:
        self._run("DELETE FROM login_failures WHERE key = ?", ("u:" + username.lower(),))

    # ---- AI usage (token counts only, no content) ------------------------
    def record_usage(self, user_id: str, kind: str, model: str, input_tokens: int, output_tokens: int,
                     cache_read_tokens: int, cache_write_tokens: int, cost_usd: float | None) -> None:
        self._run(
            "INSERT INTO ai_usage (user_id, ts, kind, model, input_tokens, output_tokens, cache_read_tokens, "
            "cache_write_tokens, cost_usd) VALUES (?,?,?,?,?,?,?,?,?)",
            (user_id, _iso(), kind, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd),
        )

    def count_ai_calls(self, user_id: str, kinds: tuple[str, ...], within_s: int) -> int:
        """How many AI calls (or blocked attempts) of these kinds the user made recently."""
        marks = ",".join("?" * len(kinds))
        rows = self._run(
            f"SELECT COUNT(*) AS n FROM ai_usage WHERE user_id = ? AND ts >= ? AND kind IN ({marks})",
            (user_id, _iso(_now() - within_s), *kinds),
        )
        return rows[0]["n"]

    def record_blocked(self, user_id: str, kind: str) -> None:
        """Count a request rejected before reaching the AI, so spamming them is rate limited too."""
        self.record_usage(user_id, kind, "none", 0, 0, 0, 0, 0.0)

    def usage_summary(self, user_id: str | None = None, month: str | None = None) -> dict:
        """Totals for a month ('YYYY-MM', default current) for one user or everyone, grouped by kind."""
        month = month or datetime.now(timezone.utc).strftime("%Y-%m")
        sql = ("SELECT kind, COUNT(*) AS calls, SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens, "
               "SUM(COALESCE(cost_usd, 0)) AS cost_usd FROM ai_usage WHERE substr(ts, 1, 7) = ?")
        params: tuple = (month,)
        if user_id is not None:
            sql += " AND user_id = ?"
            params += (user_id,)
        sql += " GROUP BY kind"
        out = {"month": month, "total_cost_usd": 0.0, "by_kind": {}}
        for r in self._run(sql, params):
            out["by_kind"][r["kind"]] = {"calls": r["calls"], "input_tokens": r["input_tokens"],
                                         "output_tokens": r["output_tokens"], "cost_usd": round(r["cost_usd"], 4)}
            out["total_cost_usd"] += r["cost_usd"]
        out["total_cost_usd"] = round(out["total_cost_usd"], 4)
        return out

    def usage_by_user(self, month: str | None = None) -> dict[str, float]:
        month = month or datetime.now(timezone.utc).strftime("%Y-%m")
        rows = self._run(
            "SELECT user_id, SUM(COALESCE(cost_usd,0)) AS c FROM ai_usage WHERE substr(ts,1,7) = ? GROUP BY user_id",
            (month,),
        )
        return {r["user_id"]: round(r["c"], 4) for r in rows}
