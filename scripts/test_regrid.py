#!/usr/bin/env python3
"""Smoke-test Regrid API for South Florida pilot counties."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lla.config import COUNTY_FIPS, require_env

REGRID_BASE = "https://app.regrid.com/api/v2"


def query_parcels(county_fips: str, limit: int = 1) -> dict:
    token = require_env("REGRID_API_KEY")
    url = f"{REGRID_BASE}/parcels/query"
    response = requests.get(
        url,
        params={
            "token": token,
            "fields[geoid][eq]": county_fips,
            "limit": limit,
            "return_geometry": "false",
            "offset_id": 0,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def main() -> None:
    results = {}
    for name, fips in COUNTY_FIPS.items():
        try:
            data = query_parcels(fips, limit=1)
            features = data.get("parcels", {}).get("features", [])
            sample = features[0]["properties"] if features else None
            results[name] = {
                "fips": fips,
                "count": data.get("parcels", {}).get("count"),
                "sample_parcel_id": sample.get("ll_uuid") if sample else None,
                "sample_address": sample.get("address") if sample else None,
                "sample_zoning": sample.get("zoning") if sample else None,
            }
        except requests.HTTPError as exc:
            results[name] = {"fips": fips, "error": str(exc), "body": exc.response.text[:500]}

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
