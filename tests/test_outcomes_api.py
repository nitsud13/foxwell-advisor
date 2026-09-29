import time
from pathlib import Path

from fastapi.testclient import TestClient

from advisor import server
from advisor.store import Store


def test_advise_logs_event_with_metrics(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(server, "store", Store(tmp_path / "a.db"))
    monkeypatch.setattr(server.jev, "api_key", "")
    c = TestClient(server.app)
    r = c.post("/advise", json={"field": "Daily budget", "old": "$349.00", "new": "$1000",
                                "entity": {"account": "1", "level": "campaigns", "id": "c1", "name": "Prospecting",
                                           "date_range": "last 7 days", "metrics": {"Amount spent": "$1,240.50", "Purchases": "18",
                                           "Purchase ROAS (return on ad spend)": "2.31", "Delivery": "Learning"}}})
    body = r.json()
    assert body["event_id"] == 1
    assert body["performance"]["spend"] == 1240.5 and body["performance"]["in_learning"] is True
    assert server.store.counts()["events"] == 1


def test_snapshot_and_judge(monkeypatch, tmp_path: Path):
    st = Store(tmp_path / "b.db")
    monkeypatch.setattr(server, "store", st)
    monkeypatch.setattr(server.jev, "api_key", "")
    c = TestClient(server.app)
    assert c.post("/snapshot", json={"account": "1", "level": "campaigns", "date_range": "w1",
                                     "rows": [{"id": "c1", "name": "P", "metrics": {"Amount spent": "$100", "Purchase ROAS": "2.0"}}]}).json()["stored"] == 1
    eid = c.post("/advise", json={"field": "Daily budget", "old": "200", "new": "600", "entity": {"account": "1", "level": "campaigns", "id": "c1", "name": "P"}}).json()["event_id"]
    c.post("/events/status", json={"event_ids": [eid], "status": "published"})
    st.conn.execute("UPDATE snapshots SET ts = ?", (time.time() - 8 * 86400,)); st.conn.execute("UPDATE events SET ts = ?", (time.time() - 7.5 * 86400,)); st.conn.commit()
    c.post("/snapshot", json={"account": "1", "level": "campaigns", "date_range": "w2",
                              "rows": [{"id": "c1", "name": "P", "metrics": {"Amount spent": "$300", "Purchase ROAS": "1.1"}}]})
    j = c.post("/outcomes/judge").json()
    assert len(j["judged"]) == 1 and j["judged"][0]["held"] in ("held", "contradicted", "inconclusive")
    assert j["judged"][0]["deltas"]["spend"]["change_pct"] == 200.0
    o = c.get("/outcomes").json()
    assert o["counts"]["outcomes"] == 1
