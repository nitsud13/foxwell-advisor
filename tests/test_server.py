from fastapi.testclient import TestClient

from advisor import server


def test_advise_stub_budget_bump(monkeypatch):
    monkeypatch.setattr(server.jev, "api_key", "")
    c = TestClient(server.app)
    r = c.post("/advise", json={"field": "daily_budget", "old": 200, "new": 600,
                                "campaign": {"name": "Prospecting", "in_learning": True}})
    assert r.status_code == 200
    body = r.json()
    assert body["delta_pct"] == 200.0
    assert body["risk"] in ("high", "severe")
    assert body["jev_mode"] == "stub"
    assert body["topic"] in server.TOPIC_BY_ID


def test_health_and_playbook():
    c = TestClient(server.app)
    assert c.get("/health").json()["ok"] is True
    pb = c.get("/playbook").json()
    assert isinstance(pb, dict)
    for v in pb.values():
        assert "chunks" not in v


def test_delta_parses_currency():
    assert server.delta_pct("$50.00", "$150.00") == 200.0
    assert server.delta_pct("1,000", "1,200") == 20.0
    assert server.delta_pct("Highest volume", "Cost cap") is None


def test_delta_only_for_money_fields():
    ev = server.ChangeEvent(field="Minimum ROAS control", old="0.010", new="2.000")
    assert server.describe(ev)["delta_pct"] is None
    ev = server.ChangeEvent(field="Daily budget", old="$349.00", new="$1000")
    assert server.describe(ev)["delta_pct"] == 186.5
