"""Runtime settings. Everything secret comes from the environment / .env.

The repo-root .env is shared with the engine; qsdash reads the same file so
there is exactly one place secrets live on a box.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(_REPO_ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- core ---------------------------------------------------------
    database_url: str = "postgresql+psycopg://quantsys:quantsys@127.0.0.1:5432/quantsys"
    redis_url: str | None = None          # None => Postgres LISTEN/NOTIFY bus
    env: str = "dev"                      # dev | prod
    public_origin: str = "https://quant.devpilotx.com"

    # --- auth ---------------------------------------------------------
    session_idle_minutes: int = 60        # sliding window
    session_absolute_hours: int = 12      # hard cap per login
    reauth_window_minutes: int = 5        # freshness needed for high-risk actions
    login_max_failures: int = 5
    login_lockout_minutes: int = 15
    cookie_secure: bool = True            # set false only for local http dev
    ip_allowlist: str = ""                # comma-separated CIDRs/IPs; empty = off

    # --- integrations -------------------------------------------------
    angel_webhook_secret: str = ""        # shared secret for postback HMAC
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # --- live-mode confirmation phrase (typed by operator, never relaxed)
    live_confirmation_phrase: str = "GO LIVE REAL MONEY"


settings = Settings()
