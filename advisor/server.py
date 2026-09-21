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


class Campaign(BaseModel):
    name: str = ""
    objective: str = ""
    status: str = ""
    days_since_launch: int | None = None
    in_learning: bool | None = None
    daily_spend: float | None = None
    bid_strategy: str = ""


class ChangeEvent(BaseModel):
    field: str = Field(..., description="field key on the mock page, or the visible label text on real Ads Manager")
    old: Any = None
    new: Any = None
    campaign: Campaign = Field(default_factory=Campaign)


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
    d = {"field": ev.field, "old": ev.old, "new": ev.new, "campaign": campaign}
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
    if ev.campaign.in_learning:
        d["description"] += "; campaign is in learning phase"
    return d


def questions() -> dict:
    return {
        "topic": choice("Which type of change is this?", topic_criteria()),
        "risk": score("How risky is this change for delivery and performance?", RISK_LEVELS),
        "interrupt": noul(
            "Is this change worth interrupting the media buyer with advice?",
            true="the change is large, unusual, or commonly regretted",
            false="routine housekeeping with little downside",
        ),
    }


@app.get("/health")
async def health() -> dict:
    pb = load_playbook()
    return {"ok": True, "jev_mode": jev.mode, "playbook_topics": len(pb),
            "playbook_source": "built" if PLAYBOOK.exists() else ("sample" if SAMPLE.exists() else "none")}


@app.get("/playbook")
async def playbook() -> dict:
    pb = load_playbook()
    return {k: {kk: vv for kk, vv in v.items() if kk != "chunks"} for k, v in pb.items()}


@app.post("/advise")
async def advise(ev: ChangeEvent) -> dict:
    t0 = time.perf_counter()
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
    return {
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
    }
