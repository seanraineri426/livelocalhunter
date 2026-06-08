from __future__ import annotations

import lla.cost_audit as cost_audit


def test_cost_audit_invalid_json_fallback(monkeypatch):
    class Response:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "not json"}}]}

    monkeypatch.setattr(cost_audit, "require_env", lambda name: "test-key")
    monkeypatch.setattr(cost_audit.requests, "post", lambda *args, **kwargs: Response())

    result = cost_audit.audit_cost_assumptions(
        parcel_context={"parcel": {"parcel_id": "p"}},
        assumptions={},
        feasibility_output={},
    )
    assert result["status"] == "unavailable"
    assert any("invalid JSON" in caveat for caveat in result["caveats"])
