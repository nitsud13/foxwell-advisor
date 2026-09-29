import json, time
from pathlib import Path

from advisor.metrics import normalize, num, delta
from advisor.store import Store


def test_num_and_normalize():
    assert num("$1,234.56") == 1234.56 and num("\u2014") is None and num("\u2014 Per Purchase") is None
    m = normalize({"Amount spent": "$193.76", "Purchase ROAS (return on ad spend)": "2.31", "Cost per result": "\u2014 Per Purchase",
                   "Delivery": "Learning", "Budget": "$349.00 Daily", "Frequency": "1.36"})
    assert m["spend"] == 193.76 and m["roas"] == 2.31 and "cpa" not in m
    assert m["in_learning"] is True and m["budget"] == 349.0 and m["frequency"] == 1.36


def test_delta():
    d = delta({"spend": 100, "roas": 2.0}, {"spend": 150, "roas": 1.5})
    assert d["spend"]["change_pct"] == 50.0 and d["roas"]["change_pct"] == -25.0


def test_store_roundtrip(tmp_path: Path):
    st = Store(tmp_path / "t.db")
    ev = {"field": "Daily budget", "old": "200", "new": "600",
          "entity": {"account": "1", "level": "campaigns", "id": "c1", "name": "Prospecting", "metrics": {"Amount spent": "$100"}}}
    eid = st.add_event(ev, {"topic": "budget_increase_large", "risk": "high", "interrupt": 0.8, "verdict": {"verdict": "cautions"}})
    assert eid == 1
    # snapshots before and after
    st.add_snapshots("1", "campaigns", "w1", [{"id": "c1", "name": "Prospecting", "metrics": {"Amount spent": "$100", "Purchase ROAS": "2.0"}}])
    st.conn.execute("UPDATE snapshots SET ts = ?", (time.time() - 8 * 86400,)); st.conn.execute("UPDATE events SET ts = ?", (time.time() - 7.5 * 86400,)); st.conn.commit()
    st.add_snapshots("1", "campaigns", "w2", [{"id": "c1", "name": "Prospecting", "metrics": {"Amount spent": "$300", "Purchase ROAS": "1.2"}}])
    pending = st.events_awaiting_outcome(7)
    assert len(pending) == 1
    before = st.snapshot_near("1", "campaigns", "Prospecting", pending[0]["ts"], after=False)
    after = st.snapshot_near("1", "campaigns", "Prospecting", pending[0]["ts"], after=True, min_gap_days=6)
    assert before and after and json.loads(after["metrics"])["Amount spent"] == "$300"
    st.add_outcome(eid, {}, {}, {"held": "held", "confidence": 0.7, "probabilities": {}, "note": ""})
    assert st.events_awaiting_outcome(7) == [] and st.outcome_summary() == {"held": 1}
