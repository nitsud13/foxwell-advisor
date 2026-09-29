"""Local advisor server.

    uvicorn advisor.server:app --port 8877 --reload

POST /advise      one change event in, one typed verdict out (~one Jev call)
GET  /playbook    the cached community playbook
GET  /health
GET  /mock/       the mock Ads Manager page (loads the extension script inline)

The live path never searches Foxwell. It classifies the change with Jev and
reads the cached playbook entry, so the round trip is one Jev call.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .jev_client import JevClient, choice, noul, score, score_level
from .metrics import delta, normalize
from .store import Store
from .topics import TOPIC_BY_ID, topic_criteria

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
PLAYBOOK = ROOT / "data" / "playbook.json"
SAMPLE = ROOT / "data" / "playbook.sample.json"

RISK_LEVELS = ["none", "low", "medium", "high", "severe"]

app = FastAPI(title="Foxwell Advisor", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def no_cache_static(request, call_next):
    """The mock page and the extension script change often during development; never cache them."""
    response = await call_next(request)
    if request.url.path.startswith(("/mock", "/extension")):
        response.headers["Cache-Control"] = "no-store"
    return response


app.mount("/mock", StaticFiles(directory=ROOT / "mock", html=True), name="mock")
app.mount("/extension", StaticFiles(directory=ROOT / "extension"), name="extension")

jev = JevClient()
store = Store()


class Campaign(BaseModel):
    name: str = ""
    objective: str = ""
    status: str = ""
    days_since_launch: int | None = None
    in_learning: bool | None = None
    daily_spend: float | None = None
    bid_strategy: str = ""


class Entity(BaseModel):
    """What the extension can read about the thing being edited: ids from the URL, metrics from the page."""
    account: str = ""
    level: str = ""          # campaigns | adsets | ads
    id: str = ""
    name: str = ""
    date_range: str = ""
    metrics: dict = Field(default_factory=dict)  # raw label -> text as shown, e.g. {"Amount spent": "$193.76"}


class ChangeEvent(BaseModel):
    field: str = Field(..., description="field key on the mock page, or the visible label text on real Ads Manager")
    old: Any = None
    new: Any = None
    campaign: Campaign = Field(default_factory=Campaign)
    entity: Entity = Field(default_factory=Entity)


class StatusUpdate(BaseModel):
    event_ids: list[int]
    status: str = Field(..., pattern="^(published|discarded|pending)$")


class SnapshotRow(BaseModel):
    id: str = ""
    name: str
    metrics: dict = Field(default_factory=dict)


class Snapshot(BaseModel):
    account: str = ""
    level: str = ""
    date_range: str = ""
    rows: list[SnapshotRow]


def load_playbook() -> dict:
    for p in (PLAYBOOK, SAMPLE):
        if p.exists():
            return json.loads(p.read_text())
    return {}


def _num(x: Any) -> float:
    """Parse '$1,500.00', '1 500', '20%' and plain numbers."""
    if isinstance(x, (int, float)):
        return float(x)
    cleaned = re.sub(r"[^0-9.\-]", "", str(x))
    return float(cleaned)


def delta_pct(old: Any, new: Any) -> float | None:
    try:
        o, n = _num(old), _num(new)
    except (TypeError, ValueError):
        return None
    if o == 0:
        return None
    return round((n - o) / o * 100, 1)


MONEY_FIELD = re.compile(r"budget|spend|amount|bid cap|cost cap|cost per result|daily|lifetime", re.I)


def describe(ev: ChangeEvent) -> dict:
    """Compact state for Jev: the change, its size, and only the campaign facts that are set.

    A percent delta only means something for money fields. A ROAS goal going from a
    placeholder 0.01 to 2.0 is not a 19900 percent increase.
    """
    campaign = {k: v for k, v in ev.campaign.model_dump().items() if v not in (None, "")}
    perf = normalize(ev.entity.metrics)
    if perf.get("in_learning") and "in_learning" not in campaign:
        campaign["in_learning"] = True
    d = {"field": ev.field, "old": ev.old, "new": ev.new, "campaign": campaign}
    if perf:
        d["performance"] = {k: v for k, v in perf.items() if k not in ("active",)}
        d["performance_window"] = ev.entity.date_range or "as shown in Ads Manager"
    d["delta_pct"] = delta_pct(ev.old, ev.new) if MONEY_FIELD.search(ev.field or "") else None
    d["description"] = f"{ev.field} changed from {ev.old} to {ev.new}"
    if d["delta_pct"] is not None:
        d["description"] += f" ({d['delta_pct']:+.0f} percent)"
        if d["delta_pct"] > 20:
            d["description"] += "; a large increase of more than 20 percent in one step"
        elif d["delta_pct"] > 0:
            d["description"] += "; a small increase of 20 percent or less"
        elif d["delta_pct"] < 0:
            d["description"] += "; a decrease, lowering the budget"
    if ev.campaign.in_learning or perf.get("in_learning"):
        d["description"] += "; campaign is in learning phase"
    if perf:
        bits = []
        if "spend" in perf: bits.append(f"spend {perf['spend']:.0f}")
        if "purchases" in perf: bits.append(f"{perf['purchases']:.0f} purchases")
        if "roas" in perf: bits.append(f"ROAS {perf['roas']:.2f}")
        if "cpa" in perf: bits.append(f"cost per result {perf['cpa']:.0f}")
        if "frequency" in perf: bits.append(f"frequency {perf['frequency']:.2f}")
        if perf.get("delivery"): bits.append(f"delivery {perf['delivery']}")
        if bits:
            d["description"] += "; performance " + (ev.entity.date_range or "shown") + ": " + ", ".join(bits)
    return d


def questions() -> dict:
    return {
        "topic": choice("Which type of change is this?", topic_criteria()),
        "risk": score("How risky is this change for delivery and performance, given the performance numbers if present?", RISK_LEVELS),
        "interrupt": noul(
            "Is this change worth interrupting the media buyer with advice?",
            true="the change is large, unusual, or commonly regretted",
            false="routine housekeeping with little downside",
        ),
    }


@app.get("/health")
async def health() -> dict:
    pb = load_playbook()
    return {"ok": True, "jev_mode": jev.mode, "playbook_topics": len(pb), "store": store.counts(),
            "playbook_source": "built" if PLAYBOOK.exists() else ("sample" if SAMPLE.exists() else "none")}


@app.get("/playbook")
async def playbook() -> dict:
    pb = load_playbook()
    return {k: {kk: vv for kk, vv in v.items() if kk != "chunks"} for k, v in pb.items()}


# Server-side guard: the same change on the same entity within a minute is a page
# re-render or a misbehaving client, not a second decision. Answer from cache, store nothing.
_recent: dict[str, tuple[float, dict]] = {}
DUPLICATE_WINDOW_S = 60


def _dupe_key(ev: ChangeEvent) -> str:
    e = ev.entity
    return f"{e.account}|{e.level}|{e.name or e.id}|{ev.field}|{ev.old}|{ev.new}"


@app.post("/advise")
async def advise(ev: ChangeEvent) -> dict:
    t0 = time.perf_counter()
    key = _dupe_key(ev)
    hit = _recent.get(key)
    if hit and time.time() - hit[0] < DUPLICATE_WINDOW_S:
        cached = dict(hit[1]); cached["duplicate"] = True; cached["event_id"] = None
        return cached
    for k in [k for k, (ts, _) in _recent.items() if time.time() - ts > DUPLICATE_WINDOW_S]:
        _recent.pop(k, None)
    state = describe(ev)
    r = await jev.ask(state, questions())
    a = r["answers"]
    topic_id = a["topic"]["choice"]
    topic = TOPIC_BY_ID.get(topic_id, TOPIC_BY_ID["other"])
    risk_idx, risk_level = score_level(a["risk"], RISK_LEVELS)
    entry = load_playbook().get(topic_id)
    verdict = None
    if entry:
        verdict = {k: v for k, v in entry.items() if k != "chunks"}
    result = {
        "topic": topic_id,
        "topic_label": topic["label"],
        "topic_confidence": a["topic"].get("confidence"),
        "topic_probabilities": a["topic"].get("probabilities"),
        "risk": risk_level,
        "risk_index": risk_idx,
        "risk_expected": a["risk"].get("score"),
        "risk_confidence": a["risk"].get("confidence"),
        "interrupt": a["interrupt"]["noul"],
        "delta_pct": state["delta_pct"],
        "verdict": verdict,
        "jev_mode": r["mode"],
        "jev_latency_ms": r["latency_ms"],
        "total_latency_ms": int((time.perf_counter() - t0) * 1000),
        "usage": r.get("usage"),
        "performance": state.get("performance"),
    }
    try:
        result["event_id"] = store.add_event(ev.model_dump(), result)
    except Exception as e:  # logging must never break advice
        result["event_id"] = None
        result["store_error"] = str(e)
    _recent[key] = (time.time(), result)
    return result


@app.post("/snapshot")
async def snapshot(snap: Snapshot) -> dict:
    """Performance rows as visible on the page. Posted on load and every few minutes."""
    n = store.add_snapshots(snap.account, snap.level, snap.date_range, [r.model_dump() for r in snap.rows])
    return {"stored": n}


@app.post("/events/status")
async def set_event_status(u: StatusUpdate) -> dict:
    """The extension calls this when the buyer clicks Publish or Discard, with the ids of the
    advised changes since the last such click. Discarded changes are never judged."""
    return {"updated": store.set_status(u.event_ids, u.status), "status": u.status}


OUTCOME = {
    "held": "performance after the change is consistent with what the community advice predicted",
    "contradicted": "performance after the change contradicts the community advice",
    "inconclusive": "too little data, too small a change, or other factors dominate",
}


@app.post("/outcomes/judge")
async def judge_outcomes(min_age_days: float = 7.0, min_gap_days: float = 6.0) -> dict:
    """Judge advised changes that are at least `min_age_days` old: compare the nearest snapshot
    before the change with the first snapshot at least `min_gap_days` after it, and ask Jev
    whether the community advice held. Snapshots come from the buyer's own page views."""
    judged, skipped = [], 0
    # Discarded edits never ran, so there is nothing to judge. Close them out.
    for ev in store.events_awaiting_outcome(min_age_days, statuses=("discarded",)):
        store.add_outcome(ev["id"], None, None, {"held": "not_applied", "confidence": 1.0, "probabilities": {}, "note": "discarded before publish"})
    for ev in store.events_awaiting_outcome(min_age_days, statuses=("published",)):
        before = store.snapshot_near(ev["account"], ev["level"], ev["entity_name"], ev["ts"], after=False)
        after = store.snapshot_near(ev["account"], ev["level"], ev["entity_name"], ev["ts"], after=True, min_gap_days=min_gap_days)
        if not before or not after:
            skipped += 1
            continue
        b = normalize(json.loads(before["metrics"])); a = normalize(json.loads(after["metrics"]))
        d = delta(b, a)
        state = {"change": f"{ev['field']}: {ev['old']} -> {ev['new']}", "topic": ev["topic"],
                 "community_verdict": ev["verdict"], "risk_at_time": ev["risk"],
                 "before_window": before["date_range"], "after_window": after["date_range"], "deltas": d}
        r = await jev.ask(state, {
            "held": choice("Did the community advice hold, judging by the before and after performance?", OUTCOME),
            "note_quality": noul("The before and after windows are comparable enough to judge",
                                 true="same window length and metric set", false="different windows or missing metrics"),
        })
        h = r["answers"]["held"]
        out = {"held": h["choice"], "confidence": h.get("confidence"), "probabilities": h.get("probabilities"),
               "note": f"comparable={r['answers']['note_quality']['noul']:.2f}"}
        store.add_outcome(ev["id"], b, a, out)
        judged.append({"event_id": ev["id"], "entity": ev["entity_name"], "change": state["change"], **out, "deltas": d})
    pending = len(store.events_awaiting_outcome(min_age_days, statuses=("pending",)))
    return {"judged": judged, "skipped_no_snapshot": skipped, "still_pending_unknown_publish": pending, "summary": store.outcome_summary()}


@app.get("/outcomes")
async def outcomes(limit: int = 50) -> dict:
    return {"events": store.recent_events(limit), "summary": store.outcome_summary(), "counts": store.counts()}
