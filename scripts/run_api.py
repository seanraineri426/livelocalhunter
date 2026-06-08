#!/usr/bin/env python3
"""Run the Live Local Hunter FastAPI app locally."""

from __future__ import annotations

import sys
from pathlib import Path

import uvicorn


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> None:
    uvicorn.run("lla.api.app:app", host="127.0.0.1", port=8000, reload=True)


if __name__ == "__main__":
    main()
