from __future__ import annotations

from fastapi.testclient import TestClient

import lla.api.app as api_app


def test_health():
    client = TestClient(api_app.app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_scenario_templates_endpoint():
    client = TestClient(api_app.app)
    response = client.get("/scenario-templates")
    assert response.status_code == 200
    assert {item["template_name"] for item in response.json()["templates"]} >= {"base_case", "conservative"}


def test_feasibility_endpoint_uses_service(monkeypatch):
    calls = {}

    def fake_compute(*, parcel_id, assumptions, template_name, run_cost_audit=False):
        calls.update(
            {
                "parcel_id": parcel_id,
                "assumptions": assumptions,
                "template_name": template_name,
                "run_cost_audit": run_cost_audit,
            }
        )
        return {"parcel_id": parcel_id, "assumptions": assumptions, "feasibility": {"result": "watch"}}

    monkeypatch.setattr(api_app, "compute_parcel_feasibility", fake_compute)
    client = TestClient(api_app.app)
    response = client.post(
        "/parcels/00000000-0000-0000-0000-000000000000/feasibility",
        json={"template_name": "base_case", "assumptions": {"market_monthly_rent": 3000}},
    )
    assert response.status_code == 200
    assert response.json()["feasibility"]["result"] == "watch"
    assert calls["template_name"] == "base_case"


def test_massing_audit_endpoint_uses_context(monkeypatch):
    monkeypatch.setattr(
        api_app,
        "build_parcel_context",
        lambda parcel_id: {
            "parcel": {"parcel_id": parcel_id, "acreage": 1, "lot_sf": 43560},
            "candidate": {"candidate_bucket": "commercial"},
            "enrichment": {"zoning_code": "MU"},
            "jurisdiction_params": {"max_far": 1.5},
            "matched_zoning_districts": [{"district_code": "MU", "category": "mixed_use", "confidence": "high"}],
            "entitlement": {
                "eligible": True,
                "max_units": 50,
                "max_height_stories": 5,
                "required_parking": 75,
                "massing_flags": [],
                "massing_inputs": {
                    "binding_constraint": "density",
                    "density_limited_units": 50,
                    "far_limited_units": 60,
                    "envelope_limited_units": 100,
                    "subject_zoning": {"matched": True, "category": "mixed_use", "confidence": "high"},
                },
            },
            "summary": {"data_gaps": []},
        },
    )
    client = TestClient(api_app.app)
    response = client.get("/parcels/00000000-0000-0000-0000-000000000000/massing-audit")
    assert response.status_code == 200
    assert response.json()["massing_audit"]["deterministic"]["sanity_status"] == "ok"


def test_massing_audit_post_passes_ai_options(monkeypatch):
    calls = {}

    monkeypatch.setattr(api_app, "build_parcel_context", lambda parcel_id: {"parcel": {"parcel_id": parcel_id}})

    def fake_run_massing_audit(context, *, use_ai=False, model=None):
        calls.update({"context": context, "use_ai": use_ai, "model": model})
        return {
            "deterministic": {"sanity_status": "ok", "flags": [], "buckets": {"ai_assisted": ["ai_massing_audit"]}},
            "ai": {"status": "reviewed", "summary": "ok", "findings": [], "human_review_items": [], "caveats": [], "model": model},
        }

    monkeypatch.setattr(api_app, "run_massing_audit", fake_run_massing_audit)
    client = TestClient(api_app.app)

    response = client.post(
        "/parcels/00000000-0000-0000-0000-000000000000/massing-audit",
        json={"use_ai": True, "model": "openrouter/test-model"},
    )

    assert response.status_code == 200
    assert calls["use_ai"] is True
    assert calls["model"] == "openrouter/test-model"
    assert response.json()["massing_audit"]["ai"]["status"] == "reviewed"
