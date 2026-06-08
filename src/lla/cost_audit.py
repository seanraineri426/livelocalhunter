"""OpenRouter structured audit for feasibility assumptions and outputs."""

from __future__ import annotations

import json
from typing import Any

import requests

from lla.config import get_env, require_env


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-4o-mini"

SYSTEM_PROMPT = """You audit Live Local financial screening assumptions.

Return strict JSON only with keys: status, findings, missing_inputs, caveats.
Do not recalculate math, invent costs, invent rents, give a legal opinion, or
state that a parcel qualifies for an exemption. Flag missing assumptions,
unsupported assumptions, and places where human underwriting or counsel must
review the deterministic calculator output."""


def _fallback(reason: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "findings": [],
        "missing_inputs": [],
        "caveats": [reason, "AI audit is advisory only; deterministic calculator output remains canonical."],
    }


def audit_cost_assumptions(
    *,
    parcel_context: dict[str, Any],
    assumptions: dict[str, Any],
    feasibility_output: dict[str, Any],
    model: str | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    api_key = require_env("OPENROUTER_API_KEY")
    selected_model = model or get_env("OPENROUTER_MODEL") or DEFAULT_MODEL
    payload = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "parcel_context": parcel_context,
                        "assumptions": assumptions,
                        "feasibility_output": feasibility_output,
                    },
                    default=str,
                    sort_keys=True,
                ),
            },
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    try:
        response = requests.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return _fallback(f"OpenRouter request failed: {exc}")
    if response.status_code != 200:
        return _fallback(f"OpenRouter returned {response.status_code}")
    try:
        content = response.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except Exception as exc:
        return _fallback(f"OpenRouter returned invalid JSON: {exc}")
    if not isinstance(parsed, dict):
        return _fallback("OpenRouter JSON was not an object")
    return {
        "status": str(parsed.get("status") or "reviewed"),
        "findings": list(parsed.get("findings") or []),
        "missing_inputs": list(parsed.get("missing_inputs") or []),
        "caveats": list(parsed.get("caveats") or []),
        "model": selected_model,
    }
