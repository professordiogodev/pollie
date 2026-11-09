"""Configuration from environment variables only (twelve-factor)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required (see .env.example)")
    return value


@dataclass(frozen=True)
class Settings:
    app_version: str
    database_url: str
    redis_url: str
    log_level: str
    log_format: str
    cors_origins: tuple[str, ...]
    max_options: int
    max_question_length: int
    results_cache_ttl: int
    worker_metrics_port: int
    worker_poll_seconds: int
    worker_max_attempts: int

    @classmethod
    def from_env(cls) -> Settings:
        from pollie import __version__

        log_format = os.environ.get("LOG_FORMAT", "json").strip().lower()
        if log_format not in {"json", "text"}:
            raise ValueError("LOG_FORMAT must be 'json' or 'text'")
        origins = tuple(o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip())
        return cls(
            app_version=os.environ.get("APP_VERSION", __version__).strip() or __version__,
            database_url=_required("DATABASE_URL"),
            redis_url=_required("REDIS_URL"),
            log_level=os.environ.get("LOG_LEVEL", "info").strip().lower(),
            log_format=log_format,
            cors_origins=origins,
            max_options=_int("MAX_OPTIONS", 10),
            max_question_length=_int("MAX_QUESTION_LENGTH", 200),
            results_cache_ttl=_int("RESULTS_CACHE_TTL", 5),
            worker_metrics_port=_int("WORKER_METRICS_PORT", 9100),
            worker_poll_seconds=_int("WORKER_POLL_SECONDS", 2),
            worker_max_attempts=_int("WORKER_MAX_ATTEMPTS", 5),
        )
