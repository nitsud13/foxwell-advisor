from pathlib import Path
from fastapi.testclient import TestClient
from advisor import server
from advisor.store import Store


def test_identical_event_within_a_minute_is_not_stored_twice(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(server, "store", Store(tmp_path / "d.db")); monkeypatch.setattr(server.jev, "api_key", ""); server._recent.clear()
    c = TestClient(server.app)
    body = {"field": "Ad on/off: X", "old": "off", "new": "on", "entity": {"account": "1", "level": "ads", "name": "X"}}
    a = c.post("/advise", json=body).json(); b = c.post("/advise", json=body).json()
    assert a["event_id"] == 1 and b.get("duplicate") is True and b["event_id"] is None
    assert server.store.counts()["events"] == 1
    body["new"] = "off"
    assert c.post("/advise", json=body).json()["event_id"] == 2
