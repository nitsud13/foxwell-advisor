"""TypeSafe Jev (System One) client with a deterministic stub.

Request shape (docs.typesafe.ai/api):

    POST https://api.typesafe.ai/v1/systemone
    Authorization: Bearer <key>
    {
      "state": <string | object | array>,
      "model": "jev-latest",
      "questions": {
        "<key>": {"type": "noul",   "instructions": "...", "criteria": {"true": "...", "false": "..."}},
        "<key>": {"type": "choice", "instructions": "...", "criteria": {"opt": "desc", ...}},   # max 255
        "<key>": {"type": "score",  "instructions": "...", "criteria": ["low", "mid", "high"]}  # 2..10
      }
    }

Response:

    {"model": "jev-1.x", "answers": {"<key>": {"type": "noul", "noul": 0.95}
                                    | {"type": "choice", "choice": "opt", "probabilities": {...}, "confidence": 0.8}
                                    | {"type": "score", "score": 2, "probabilities": {...}, "confidence": 0.7}},
     "usage": {"input_tokens": 296, "output_tokens": 20}}

All questions in one request run in parallel and in isolation. Jev never
generates free text, so anything human-readable is written by this codebase,
not the model.

Stub mode (no TYPESAFE_API_KEY): answers come from keyword overlap between the
state and each option's description, so the demo runs end to end before the
key arrives. Stub answers are labelled `"stub": true`.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from typing import Any

import httpx

WORD_RE = re.compile(r"[a-z0-9]+")


def noul(instructions: str, true: str | None = None, false: str | None = None) -> dict:
    q: dict = {"type": "noul", "instructions": instructions}
    if true or false:
        q["criteria"] = {"true": true or "", "false": false or ""}
    return q


def choice(instructions: str, criteria: dict[str, str]) -> dict:
    if not 2 <= len(criteria) <= 255:
        raise ValueError("choice needs 2..255 options")
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def score_level(answer: dict, levels: list[str]) -> tuple[int, str]:
    """Most likely level for a score answer. Live Jev returns `score` as an expected
    value (float) plus `probabilities` keyed by level index; use the argmax."""
    probs = answer.get("probabilities") or {}
    if probs:
        idx = int(max(probs, key=probs.get))
    else:
        idx = int(round(float(answer.get("score", 0))))
    idx = max(0, min(len(levels) - 1, idx))
    return idx, levels[idx]


def score(instructions: str, criteria: list[str]) -> dict:
    if not 2 <= len(criteria) <= 10:
        raise ValueError("score needs 2..10 levels")
    return {"type": "score", "instructions": instructions, "criteria": list(criteria)}


class JevClient:
    def __init__(
        self,
        api_key: str | None = None,
        api_url: str | None = None,
        model: str | None = None,
        timeout: float = 10.0,
    ):
        self.api_key = api_key if api_key is not None else os.environ.get("TYPESAFE_API_KEY", "")
        self.api_url = api_url or os.environ.get("TYPESAFE_API_URL", "https://api.typesafe.ai/v1/systemone")
        self.model = model or os.environ.get("TYPESAFE_MODEL", "jev-latest")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    @property
    def mode(self) -> str:
        return "live" if self.api_key else "stub"

    async def ask(self, state: Any, questions: dict[str, dict]) -> dict:
        """Return {"answers": {...}, "usage": {...}, "model": str, "latency_ms": int, "mode": str}."""
        t0 = time.perf_counter()
        if not self.api_key:
            answers = {k: _stub_answer(state, q) for k, q in questions.items()}
            out = {"model": "stub", "answers": answers, "usage": {"input_tokens": 0, "output_tokens": 0}}
        else:
            out = await self._post({"state": state, "model": self.model, "questions": questions})
        out["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        out["mode"] = self.mode
        return out

    async def _post(self, body: dict) -> dict:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        delay = 0.5
        for attempt in range(4):
            r = await self._client.post(self.api_url, headers=headers, json=body)
            if r.status_code in (429, 529) and attempt < 3:
                import asyncio

                await asyncio.sleep(delay)
                delay *= 2
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError("unreachable")

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()


# ---------------------------------------------------------------- stub ----

STOP = {"a", "an", "the", "or", "and", "to", "of", "in", "on", "by", "is", "it", "that", "this", "with",
        "for", "from", "than", "one", "off", "as", "at", "be", "are", "was", "not", "no", "yes", "such"}


def _tokens(x: Any) -> set[str]:
    if not isinstance(x, str):
        x = json.dumps(x, default=str)
    toks = {t for t in WORD_RE.findall(x.lower()) if t not in STOP and len(t) > 1}
    return {t[:-1] if t.endswith("s") and len(t) > 3 else t for t in toks}  # crude plural fold


def _softmax(scores: dict[str, float], temp: float = 0.6) -> dict[str, float]:
    if not scores:
        return {}
    m = max(scores.values())
    exp = {k: math.exp((v - m) / temp) for k, v in scores.items()}
    z = sum(exp.values())
    return {k: round(v / z, 4) for k, v in exp.items()}


def _stub_answer(state: Any, q: dict) -> dict:
    st = _tokens(state)
    kind = q["type"]
    if kind == "noul":
        crit = q.get("criteria") or {}
        t = len(st & _tokens(crit.get("true", ""))) if crit else 0
        f = len(st & _tokens(crit.get("false", ""))) if crit else 0
        p = 0.5 + 0.15 * (t - f)
        return {"type": "noul", "noul": round(min(0.95, max(0.05, p)), 3), "stub": True}
    if kind == "choice":
        crit: dict[str, str] = q["criteria"]
        raw = {k: float(len(st & (_tokens(k) | _tokens(v)))) for k, v in crit.items()}
        probs = _softmax(raw)
        best = max(probs, key=probs.get)
        conf = round(probs[best], 3)
        return {"type": "choice", "choice": best, "probabilities": probs, "confidence": conf, "stub": True}
    if kind == "score":
        levels: list[str] = q["criteria"]
        # If the state carries a numeric hint named like the question, use it.
        hint = None
        if isinstance(state, dict):
            for key in ("delta_pct", "risk_hint"):
                if isinstance(state.get(key), (int, float)):
                    hint = abs(float(state[key]))
                    break
        if hint is None:
            idx = len(levels) // 2
        else:
            # 0..100 percent change maps across levels; >100 pins to top.
            idx = min(len(levels) - 1, int(hint / (100 / len(levels))))
        raw = {str(i): -abs(i - idx) * 1.0 for i in range(len(levels))}
        probs = _softmax(raw, temp=0.5)
        expected = round(sum(int(k) * v for k, v in probs.items()), 2)
        return {"type": "score", "score": expected, "probabilities": probs,
                "legend": {str(i): lvl for i, lvl in enumerate(levels)},
                "confidence": round(probs[str(idx)], 3), "stub": True}
    raise ValueError(f"unknown question type {kind}")
