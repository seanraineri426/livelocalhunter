from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


def load_env() -> None:
    load_dotenv(ROOT / ".env.local")
    load_dotenv(ROOT / ".env")


def require_env(name: str) -> str:
    load_env()
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def get_env(name: str, default: str | None = None) -> str | None:
    load_env()
    return os.getenv(name, default)


# South Florida pilot counties (FIPS)
COUNTY_FIPS = {
    "miami_dade": "12086",
    "broward": "12011",
    "palm_beach": "12099",
}
