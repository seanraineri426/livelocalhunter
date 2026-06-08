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


def test_parcel_geometry_endpoint_returns_feature(monkeypatch):
    row = {
        "parcel_id": "00000000-0000-0000-0000-000000000000",
        "folio": "3530210010010",
        "site_address": "123 Main St",
        "site_city": "Doral",
        "site_zip": "33178",
        "is_candidate": True,
        "candidate_bucket": "commercial",
        "eligible": True,
        "review_status": None,
        "geometry": '{"type":"Polygon","coordinates":[[[-80.1,25.9],[-80.0,25.9],[-80.0,26.0],[-80.1,25.9]]]}',
    }

    class FakeResult:
        def mappings(self):
            return self

        def first(self):
            return row

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, _query, params):
            assert params["parcel_id"] == row["parcel_id"]
            return FakeResult()

    class FakeEngine:
        def connect(self):
            return FakeConnection()

    monkeypatch.setattr(api_app, "get_engine", lambda: FakeEngine())
    client = TestClient(api_app.app)

    response = client.get(f"/parcels/{row['parcel_id']}/geometry")

    assert response.status_code == 200
    feature = response.json()
    assert feature["type"] == "Feature"
    assert feature["geometry"]["type"] == "Polygon"
    assert feature["properties"] == {
        "parcel_id": row["parcel_id"],
        "folio": row["folio"],
        "eligible": True,
        "status": "eligible",
        "address": row["site_address"],
        "city": row["site_city"],
        "zip": row["site_zip"],
        "is_candidate": True,
        "candidate_bucket": "commercial",
    }




def test_search_parcels_accepts_county_slug_variants(monkeypatch):
    calls = {}

    class FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def mappings(self):
            return self

        def __iter__(self):
            return iter(self._rows)

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, _query, params):
            calls.update(params)
            return FakeResult([])

    class FakeEngine:
        def connect(self):
            return FakeConnection()

    monkeypatch.setattr(api_app, "get_engine", lambda: FakeEngine())
    client = TestClient(api_app.app)

    for county in ("Broward", "broward", "miami_dade", "Miami-Dade", "12086"):
        calls.clear()
        response = client.get(f"/parcels/search?county={county}&folio=3530210010010")
        assert response.status_code == 200
        assert calls["county_fips"] in {"12011", "12086"}
def test_identify_parcel_endpoint_returns_compact_match(monkeypatch):
    row = {
        "parcel_id": "00000000-0000-0000-0000-000000000000",
        "county_fips": "12086",
        "source_parcel_id": "3530210010010",
        "source_parcel_id_normalized": "3530210010010",
        "site_address": "123 Main St",
        "site_city": "Doral",
        "site_zip": "33178",
        "lot_sf": 43560,
        "acreage": 1,
        "zoning_code": "IU-C",
        "use_class": "commercial",
        "is_candidate": True,
        "candidate_bucket": "commercial",
        "candidate_reason": "candidate use",
        "normalized_use": "commercial",
        "jurisdiction": "Doral",
        "eligible": True,
        "failed_reasons": [],
        "max_units": 75,
        "confidence": "high",
        "massing_flags": [],
        "review_status": None,
        "geom_area_sf": 43560,
    }

    class FakeResult:
        def mappings(self):
            return self

        def first(self):
            return row

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, params):
            query_text = str(query)
            assert "p.geom && pt.geom" in query_text
            assert "ST_Contains(p.geom, pt.geom) OR ST_Intersects(p.geom, pt.geom)" in query_text
            assert params == {"lng": -80.35, "lat": 25.82}
            return FakeResult()

    class FakeEngine:
        def connect(self):
            return FakeConnection()

    monkeypatch.setattr(api_app, "get_engine", lambda: FakeEngine())
    client = TestClient(api_app.app)

    response = client.get("/parcels/identify?lng=-80.35&lat=25.82")

    assert response.status_code == 200
    payload = response.json()
    assert payload["parcel_id"] == row["parcel_id"]
    assert payload["source_parcel_id"] == row["source_parcel_id"]
    assert payload["jurisdiction"] == "Doral"
    assert payload["eligible"] is True
    assert payload["confidence"] == "high"
    assert payload["max_units"] == 75
    assert payload["status"] == "eligible"


def test_identify_parcel_endpoint_returns_404_when_no_match(monkeypatch):
    class FakeResult:
        def mappings(self):
            return self

        def first(self):
            return None

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, _query, _params):
            return FakeResult()

    class FakeEngine:
        def connect(self):
            return FakeConnection()

    monkeypatch.setattr(api_app, "get_engine", lambda: FakeEngine())
    client = TestClient(api_app.app)

    response = client.get("/parcels/identify?lng=-80.35&lat=25.82")

    assert response.status_code == 404
    assert response.json()["detail"] == "No parcel found at this location."


def test_identify_parcel_endpoint_validates_coordinates():
    client = TestClient(api_app.app)

    response = client.get("/parcels/identify?lng=-181&lat=25.82")

    assert response.status_code == 422


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
