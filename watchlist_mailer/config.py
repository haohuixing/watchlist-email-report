"""Paths, environment loading and runtime settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "out"
LOGS_DIR = ROOT / "logs"
ENV_FILE = ROOT / ".env"
WATCHLISTS_PATH = ROOT / "watchlists.txt"

SNAPSHOT_PATH = DATA_DIR / "snapshot.json"
WATCHLIST_TXT_PATH = DATA_DIR / "watchlist.txt"
STATE_PATH = DATA_DIR / "state.json"
SERVE_LOCK = DATA_DIR / "serve.lock"

ET_NAME = "America/New_York"
SLOT_TIMES = {"open": time(11, 0), "close": time(16, 0)}
SLOT_LABELS = {"open": "11:00 AM ET", "close": "Market close"}
SLOT_WINDOW_MINUTES = 30
DEFAULT_REPORT_BATCH_SIZE = 50


class ConfigError(RuntimeError):
    """Raised when required settings are missing."""


def load_env(path: Path | None = None) -> None:
    """Load KEY=VALUE lines without overriding variables already in the environment."""
    path = ENV_FILE if path is None else path
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


@dataclass(frozen=True)
class Settings:
    smtp_username: str
    smtp_password: str
    mail_to: tuple[str, ...]
    mail_from: str


def load_settings(require_mail: bool = True, env_path: Path | None = None) -> Settings:
    load_env(env_path)
    username = os.environ.get("SMTP_USERNAME", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "").strip().replace(" ", "")
    recipients = tuple(
        recipient.strip()
        for recipient in os.environ.get("MAIL_TO", "").split(",")
        if recipient.strip()
    )
    mail_from = os.environ.get("MAIL_FROM", "").strip() or username

    if require_mail:
        missing = [
            name
            for name, value in (
                ("SMTP_USERNAME", username),
                ("SMTP_PASSWORD", password),
                ("MAIL_TO", recipients),
            )
            if not value
        ]
        if missing:
            raise ConfigError(
                "Missing "
                + ", ".join(missing)
                + ". Copy .env.example to .env and fill it in."
            )
    return Settings(username, password, recipients, mail_from)


def report_batch_size() -> int:
    """Rows per email; override with REPORT_BATCH_SIZE after .env is loaded."""
    raw = os.environ.get("REPORT_BATCH_SIZE", "").strip()
    try:
        value = int(raw) if raw else DEFAULT_REPORT_BATCH_SIZE
    except ValueError:
        value = DEFAULT_REPORT_BATCH_SIZE
    return max(1, min(500, value))


def ensure_dirs() -> None:
    for directory in (DATA_DIR, OUT_DIR, LOGS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
