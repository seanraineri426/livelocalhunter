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
