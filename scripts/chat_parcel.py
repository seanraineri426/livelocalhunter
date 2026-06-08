#!/usr/bin/env python3
"""Interactive chat over one Live Local parcel context packet."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lla.config import COUNTY_FIPS  # noqa: E402
from lla.parcel_chat import ParcelChatError, chat_about_parcel  # noqa: E402
from lla.parcel_context import ParcelContextError, build_parcel_context, to_context_json  # noqa: E402


LOG_PATH = Path("/tmp/lla_parcel_chat.log")


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, mode="a"),
            logging.StreamHandler(sys.stderr),
        ],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    lookup = parser.add_mutually_exclusive_group(required=True)
    lookup.add_argument("--parcel-id", help="Internal lla.parcels.parcel_id UUID")
    lookup.add_argument("--folio", help="County folio/source parcel id")
    parser.add_argument("--county", choices=sorted(COUNTY_FIPS), help="County key for folio lookup")
    parser.add_argument("--context-only", action="store_true", help="Print parcel context JSON and exit")
    parser.add_argument("--model", help="OpenRouter model override")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging()

    try:
        context = build_parcel_context(parcel_id=args.parcel_id, folio=args.folio, county=args.county)
    except ParcelContextError as exc:
        logging.error("Context build failed: %s", exc)
        raise SystemExit(1) from exc

    logging.info("Loaded context for parcel_id=%s folio=%s county=%s", args.parcel_id, args.folio, args.county)
    logging.info("Context summary: %s", context["summary"]["identity"])

    if args.context_only:
        print(to_context_json(context))
        logging.info("Wrote context-only output")
        return

    print(context["summary"]["identity"])
    print("Ask about this parcel. Type 'exit' or Ctrl-D to quit.")

    messages: list[dict[str, str]] = []
    while True:
        try:
            question = input("\nparcel> ").strip()
        except EOFError:
            print()
            break
        if not question:
            continue
        if question.lower() in {"exit", "quit", ":q"}:
            break

        messages.append({"role": "user", "content": question})
        logging.info("USER parcel_id=%s question=%s", context["parcel"]["parcel_id"], question)
        try:
            result = chat_about_parcel(
                parcel_id=context["parcel"]["parcel_id"],
                messages=messages,
                model=args.model,
            )
        except ParcelChatError as exc:
            logging.error("Chat failed: %s", exc)
            print(f"Chat failed: {exc}", file=sys.stderr)
            messages.pop()
            continue

        answer = result["message"].strip()
        print(f"\n{answer}")
        logging.info("ASSISTANT parcel_id=%s answer=%s", context["parcel"]["parcel_id"], answer)
        messages.append({"role": "assistant", "content": answer})

    logging.info("Chat session ended for parcel_id=%s log=%s", context["parcel"]["parcel_id"], LOG_PATH)


if __name__ == "__main__":
    main()
