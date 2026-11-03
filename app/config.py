"""Application settings, read from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader: KEY=VALUE lines; never overrides variables already set."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# USD per million tokens: (input, output). Cache reads are billed at 0.1x input,
# 5-minute cache writes at 1.25x input.
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    anthropic_api_key: str | None = None
    extract_model: str = "claude-sonnet-5-5"
    query_model: str = "claude-haiku-5-5"
    card_gate_model: str = "claude-haiku-5-5"
    extract_fallbacks: bool = True
    cookie_secure: bool = True
    session_days: int = 30
    admin_username: str | None = None
    admin_password: str | None = None
    max_upload_mb: int = 15
    # Abuse limits, per user. 0 disables a limit.
    cards_per_day: int = 100
    queries_per_day: int = 300
    ai_calls_per_minute: int = 10
    max_pending_jobs: int = 3
    model_prices: dict[str, tuple[float, float]] = field(default_factory=lambda: dict(MODEL_PRICES))

    @property
    def users_dir(self) -> Path:
        return self.data_dir / "users"

    @property
    def accounts_db(self) -> Path:
        return self.data_dir / "auth.db"

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls(
            data_dir=Path(os.environ.get("DATA_DIR", "./data")).resolve(),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
            extract_model=os.environ.get("EXTRACT_MODEL", "claude-sonnet-5-5"),
            query_model=os.environ.get("QUERY_MODEL", "claude-haiku-5-5"),
            card_gate_model=os.environ.get("CARD_GATE_MODEL", "claude-haiku-5-5"),
            extract_fallbacks=_bool("EXTRACT_FALLBACKS", True),
            cookie_secure=_bool("COOKIE_SECURE", True),
            session_days=int(os.environ.get("SESSION_DAYS", "30")),
            admin_username=os.environ.get("ADMIN_USERNAME") or None,
            admin_password=os.environ.get("ADMIN_PASSWORD") or None,
            max_upload_mb=int(os.environ.get("MAX_UPLOAD_MB", "15")),
            cards_per_day=int(os.environ.get("CARDS_PER_DAY", "100")),
            queries_per_day=int(os.environ.get("QUERIES_PER_DAY", "300")),
            ai_calls_per_minute=int(os.environ.get("AI_CALLS_PER_MINUTE", "10")),
            max_pending_jobs=int(os.environ.get("MAX_PENDING_JOBS", "3")),
        )


def estimate_cost(
    prices: dict[str, tuple[float, float]],
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float | None:
    if model not in prices:
        return None
    pin, pout = prices[model]
    return (
        input_tokens * pin
        + output_tokens * pout
        + cache_read_tokens * pin * 0.1
        + cache_write_tokens * pin * 1.25
    ) / 1_000_000
