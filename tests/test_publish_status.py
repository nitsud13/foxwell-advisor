import time
from pathlib import Path

from fastapi.testclient import TestClient

from advisor import server
from advisor.store import Store


_n = [0]


def _advise(c, name="P"):
    # distinct "new" values so the server's duplicate guard does not collapse them
    _n[0] += 1
    return c.post("/advise", json={"field": "Daily budget", "old": "200", "new": str(600 + _n[0]),
                                   "entity": {"account": "1", "level": "campaigns", "id": "c1", "name": name}}).json()["event_id"]


def test_status_flow_and_judge_only_published(monkeypatch, tmp_path: Path):
    st = Store(tmp_path / "s.db"); monkeypatch.setattr(server, "store", st); monkeypatch.setattr(server.jev, "api_key", ""); server._recent.clear()
    c = TestClient(server.app)
    c.post("/snapshot", json={"account": "1", "level": "campaigns", "date_range": "w1", "rows": [{"name": "P", "metrics": {"Amount spent": "$100"}}]})
    e_pub, e_disc, e_pend = _advise(c), _advise(c), _advise(c)
    assert c.post("/events/status", json={"event_ids": [e_pub], "status": "published"}).json()["updated"] == 1
    assert c.post("/events/status", json={"event_ids": [e_disc], "status": "discarded"}).json()["updated"] == 1
    # a second click cannot flip a decided event
    assert c.post("/events/status", json={"event_ids": [e_pub], "status": "discarded"}).json()["updated"] == 0
    st.conn.execute("UPDATE events SET ts = ?", (time.time() - 8 * 86400,)); st.conn.execute("UPDATE snapshots SET ts = ?", (time.time() - 9 * 86400,)); st.conn.commit()
    c.post("/snapshot", json={"account": "1", "level": "campaigns", "date_range": "w2", "rows": [{"name": "P", "metrics": {"Amount spent": "$300"}}]})
    j = c.post("/outcomes/judge").json()
    assert [x["event_id"] for x in j["judged"]] == [e_pub]
    assert j["still_pending_unknown_publish"] == 1
    assert j["summary"].get("not_applied") == 1
    assert st.status_counts() == {"published": 1, "discarded": 1, "pending": 1}
