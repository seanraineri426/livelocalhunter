#!/usr/bin/env python3
"""Build and optionally write a parcel intelligence context JSON packet."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lla.config import COUNTY_FIPS  # noqa: E402
from lla.parcel_context import build_parcel_context, to_context_json  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    lookup = parser.add_mutually_exclusive_group(required=True)
    lookup.add_argument("--parcel-id", help="Internal lla.parcels.parcel_id UUID")
    lookup.add_argument("--folio", help="County folio/source parcel id")
    parser.add_argument("--county", choices=sorted(COUNTY_FIPS), help="County key for folio lookup")
    parser.add_argument("--output", type=Path, help="Write context JSON to this path instead of stdout")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    context = build_parcel_context(parcel_id=args.parcel_id, folio=args.folio, county=args.county)
    payload = to_context_json(context)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n")
        print(f"Wrote {args.output}")
        return
    print(payload)


if __name__ == "__main__":
    main()
