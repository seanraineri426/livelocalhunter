"""Explain parcel intelligence packets with an OpenRouter chat model."""

from __future__ import annotations

from typing import Any, Iterable

import requests

from lla.config import get_env, require_env
from lla.parcel_context import build_parcel_context, to_context_json


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-4o-mini"

SYSTEM_PROMPT = """You are a Live Local Act parcel intelligence assistant.

You explain only the provided parcel context JSON. Do not invent eligibility,
massing, zoning, parking, height, unit-count, address, ownership, legal, or
financial facts. Cite the specific context fields, flags, inputs, failed reasons,
and data gaps that support your answer. If the context is missing a fact, say it
is missing and explain what counsel, zoning staff, or an analyst should verify.
Do not give a final legal conclusion; frame legal issues as verification items.
Keep answers concise and practical for acquisition/development diligence."""


class ParcelChatError(RuntimeError):
    """Raised when parcel chat cannot complete."""


def _normalize_messages(messages: Iterable[dict[str, str]] | None) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for message in messages or []:
        role = message.get("role")
        content = message.get("content")
        if role not in {"user", "assistant"}:
            raise ParcelChatError(f"Unsupported message role: {role!r}")
        if not content:
            continue
        normalized.append({"role": role, "content": str(content)})
    return normalized


def _request_payload(
    *,
    context: dict[str, Any],
    messages: list[dict[str, str]],
    model: str | None,
    temperature: float,
) -> dict[str, Any]:
    selected_model = model or get_env("OPENROUTER_MODEL") or DEFAULT_MODEL
    context_json = to_context_json(context)
    return {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Use this parcel context JSON as the only factual source for the conversation:\n"
                    f"```json\n{context_json}\n```"
                ),
            },
            *messages,
        ],
        "temperature": temperature,
    }


def chat_about_parcel(
    parcel_id: str | None = None,
    messages: Iterable[dict[str, str]] | None = None,
    *,
    folio: str | None = None,
    county: str | None = None,
    model: str | None = None,
    temperature: float = 0.1,
    timeout: int = 120,
) -> dict[str, Any]:
    """Return an assistant response grounded in a parcel context packet."""

    api_key = require_env("OPENROUTER_API_KEY")
    context = build_parcel_context(parcel_id, folio=folio, county=county)
    normalized_messages = _normalize_messages(messages)
    if not normalized_messages:
        normalized_messages = [{"role": "user", "content": "Summarize this parcel for Live Local diligence."}]

    payload = _request_payload(
        context=context,
        messages=normalized_messages,
        model=model,
        temperature=temperature,
    )
    response = requests.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    if response.status_code != 200:
        raise ParcelChatError(f"openrouter {response.status_code}: {response.text[:500]}")

    data = response.json()
    content = data["choices"][0]["message"]["content"]
    return {
        "parcel_id": context["parcel"]["parcel_id"],
        "model": payload["model"],
        "message": content,
        "context": context,
        "raw": data,
    }


def stream_chat_about_parcel(
    parcel_id: str | None = None,
    messages: Iterable[dict[str, str]] | None = None,
    *,
    folio: str | None = None,
    county: str | None = None,
    model: str | None = None,
    temperature: float = 0.1,
    timeout: int = 120,
):
    """Yield raw OpenRouter streaming lines for callers that want streaming output."""

    api_key = require_env("OPENROUTER_API_KEY")
    context = build_parcel_context(parcel_id, folio=folio, county=county)
    payload = _request_payload(
        context=context,
        messages=_normalize_messages(messages),
        model=model,
        temperature=temperature,
    )
    payload["stream"] = True
    with requests.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
        stream=True,
    ) as response:
        if response.status_code != 200:
            raise ParcelChatError(f"openrouter {response.status_code}: {response.text[:500]}")
        for line in response.iter_lines(decode_unicode=True):
            if line:
                yield line
