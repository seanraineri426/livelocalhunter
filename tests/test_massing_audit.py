from __future__ import annotations

import lla.massing_audit as massing_audit
from lla.massing_audit import deterministic_massing_audit


def _context(**overrides):
    context = {
        "parcel": {
            "parcel_id": "p-1",
            "acreage": 1.25,
            "lot_sf": 54450,
        },
        "candidate": {
            "candidate_bucket": "commercial",
            "normalized_use": "commercial",
        },
        "enrichment": {
            "zoning_code": "MU-1",
            "zoning_general_use": "mixed use",
        },
        "jurisdiction_params": {
            "max_far": 1.5,
        },
        "matched_zoning_districts": [
            {
                "district_code": "MU-1",
                "category": "mixed_use",
                "confidence": "high",
            }
        ],
        "entitlement": {
            "eligible": True,
            "failed_reasons": [],
            "max_units": 70,
            "max_height_stories": 6,
            "buildable_sf": 75000,
            "required_parking": 105,
            "massing_flags": [],
            "massing_inputs": {
                "binding_constraint": "far",
                "density_limited_units": 94,
                "far_limited_units": 70,
                "envelope_limited_units": 180,
                "footprint_sf": "18000",
                "surface_parking_sf_estimate": "30000",
                "max_height_stories": "6",
                "far": "1.5",
                "land_category": "commercial",
                "parcel_zoning_confidence": "high",
                "subject_zoning": {
                    "matched": True,
                    "category": "mixed_use",
                    "confidence": "high",
                },
            },
        },
        "summary": {
            "data_gaps": [],
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(context.get(key), dict):
            context[key] = {**context[key], **value}
        else:
            context[key] = value
    return context


def _flag_ids(audit):
    return {flag["id"] for flag in audit["flags"]}


def test_ineligible_is_not_applicable():
    audit = deterministic_massing_audit(
        _context(entitlement={"eligible": False, "failed_reasons": ["not_live_local_land_use"]})
    )
    assert audit["sanity_status"] == "not_applicable"
    assert "massing_not_applicable_ineligible" in _flag_ids(audit)


def test_huge_units_flagged_for_review():
    context = _context(
        parcel={"acreage": 10, "lot_sf": 435600},
        entitlement={
            "eligible": True,
            "max_units": 6000,
            "max_height_stories": 8,
            "massing_inputs": {
                "binding_constraint": "density",
                "density_limited_units": 6000,
                "far_limited_units": 8000,
                "envelope_limited_units": 9000,
                "subject_zoning": {"matched": True, "category": "mixed_use", "confidence": "high"},
            },
        },
    )
    audit = deterministic_massing_audit(context)
    assert audit["sanity_status"] == "review"
    assert "implausibly_large_unit_count" in _flag_ids(audit)


def test_oversized_tract_requires_manual_boundary():
    audit = deterministic_massing_audit(_context(parcel={"acreage": 75, "lot_sf": 3267000}))
    assert "manual_site_boundary_required" in _flag_ids(audit)
    assert audit["sanity_status"] == "review"
    assert "manual_site_boundary_required" in audit["buckets"]["human_required"]


def test_low_density_commercial_output_is_suspicious():
    audit = deterministic_massing_audit(
        _context(
            parcel={"acreage": 3, "lot_sf": 130680},
            entitlement={"eligible": True, "max_units": 2},
        )
    )
    assert "suspiciously_low_density_live_local" in _flag_ids(audit)


def test_missing_binding_constraint_flagged():
    context = _context()
    context["entitlement"]["massing_inputs"] = {
        key: value
        for key, value in context["entitlement"]["massing_inputs"].items()
        if key != "binding_constraint"
    }
    audit = deterministic_massing_audit(context)
    assert "binding_constraint_missing" in _flag_ids(audit)


def test_zero_units_eligible_is_likely_bad_input():
    audit = deterministic_massing_audit(_context(entitlement={"eligible": True, "max_units": 0}))
    assert audit["sanity_status"] == "likely_bad_input"
    assert "eligible_nonpositive_units" in _flag_ids(audit)


def test_sane_normal_parcel_is_ok():
    audit = deterministic_massing_audit(_context())
    assert audit["sanity_status"] == "ok"
    assert audit["flags"] == []


def test_ai_massing_audit_invalid_json_fallback(monkeypatch):
    class Response:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "not json"}}]}

    monkeypatch.setattr(massing_audit, "require_env", lambda name: "test-key")
    monkeypatch.setattr(massing_audit.requests, "post", lambda *args, **kwargs: Response())

    result = massing_audit.ai_massing_audit(_context(), {"sanity_status": "ok"})
    assert result["status"] == "unavailable"
    assert any("invalid JSON" in caveat for caveat in result["caveats"])


def test_ai_massing_audit_missing_key_falls_back_without_request(monkeypatch):
    def fail_post(*args, **kwargs):
        raise AssertionError("OpenRouter should not be called without a key")

    monkeypatch.setattr(massing_audit, "require_env", lambda name: (_ for _ in ()).throw(RuntimeError("missing key")))
    monkeypatch.setattr(massing_audit.requests, "post", fail_post)

    result = massing_audit.ai_massing_audit(_context(), {"sanity_status": "ok"})

    assert result["status"] == "unavailable"
    assert result["model"] == massing_audit.DEFAULT_MODEL
    assert any("OpenRouter is not configured" in caveat for caveat in result["caveats"])


def test_ai_massing_audit_posts_openrouter_json_request(monkeypatch):
    calls = {}

    class Response:
        status_code = 200

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"status":"reviewed","summary":"Looks reasonable.","findings":"No extra calculation.","human_review_items":[],"caveats":"Advisory only."}'
                        }
                    }
                ]
            }

    def fake_post(url, *, headers, json, timeout):
        calls.update({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return Response()

    monkeypatch.setattr(massing_audit, "require_env", lambda name: "test-openrouter-key")
    monkeypatch.setattr(massing_audit.requests, "post", fake_post)

    result = massing_audit.ai_massing_audit(_context(), {"sanity_status": "ok"}, model="test/model", timeout=9)

    assert result["status"] == "reviewed"
    assert result["model"] == "test/model"
    assert result["findings"] == ["No extra calculation."]
    assert result["caveats"] == ["Advisory only."]
    assert calls["url"] == massing_audit.OPENROUTER_URL
    assert calls["headers"]["Authorization"] == "Bearer test-openrouter-key"
    assert calls["json"]["model"] == "test/model"
    assert calls["json"]["response_format"] == {"type": "json_object"}
    assert calls["timeout"] == 9
