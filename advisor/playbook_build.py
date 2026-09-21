"""Build the playbook: one Foxwell search per topic, one Jev call per chunk.

    python -m advisor.playbook_build            # all topics
    python -m advisor.playbook_build --topics budget_increase_large,bid_cost_cap
    python -m advisor.playbook_build --dry-run  # search only, no Jev, print counts

Output: data/playbook.json  { topic_id: PlaybookEntry }
Raw search results are kept in data/raw/<topic>.json so you can re-run Jev
without hitting the Foxwell rate limit again (--from-raw).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from .foxwell_client import Chunk, FoxwellClient, parse_search_text
from .jev_client import JevClient, choice, noul
from .topics import TOPICS, TOPIC_BY_ID

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
PLAYBOOK = DATA / "playbook.json"

CONDITION = {
    "spend_level": "it depends on daily spend or budget size",
    "account_size": "it depends on account maturity, history, or pixel data volume",
    "creative": "it depends on creative volume, quality, or fatigue",
    "product_or_offer": "it depends on the product, price point, margin, or offer",
    "seasonality": "it depends on timing, season, or sale periods",
    "none": "no condition is stated",
}

STANCE = {
    "supports": "the text agrees with or recommends the statement",
    "cautions": "the text disagrees with, warns against, or reports bad results from the statement",
    "depends": "the text says it depends on conditions such as spend level, account size, or pixel data",
    "unrelated": "the text does not address the statement",
}


def chunk_questions(topic: dict) -> dict:
    return {
        "stance": choice(f"Statement: {topic['question']}. What is this text's position on the statement?", STANCE),
        "actionable": noul(
            "The text contains a concrete recommendation or a specific number someone could act on",
            true="specific tactic, threshold, number, or step",
            false="general discussion, question, or anecdote without a recommendation",
        ),
        "firsthand": noul(
            "The author reports their own test or account results rather than repeating advice",
            true="describes what happened in their own account or test",
            false="repeats advice or asks a question",
        ),
        "condition": choice("If the text says the answer depends on something, what does it hinge on?", CONDITION),
    }


async def classify_chunks(jev: JevClient, topic: dict, chunks: list[Chunk], concurrency: int = 4) -> list[dict]:
    sem = asyncio.Semaphore(concurrency)

    async def one(c: Chunk) -> dict:
        async with sem:
            r = await jev.ask({"text": c.text, "source": c.source, "date": c.date}, chunk_questions(topic))
        a = r["answers"]
        d = c.to_dict()
        d["stance"] = a["stance"]["choice"]
        d["stance_confidence"] = a["stance"].get("confidence")
        d["stance_probabilities"] = a["stance"].get("probabilities")
        d["actionable"] = a["actionable"]["noul"]
        d["firsthand"] = a["firsthand"]["noul"]
        d["condition"] = a["condition"]["choice"]
        d["condition_confidence"] = a["condition"].get("confidence")
        d["jev_latency_ms"] = r["latency_ms"]
        return d

    return await asyncio.gather(*(one(c) for c in chunks))


NOISE = [
    re.compile(r"^\[#[\w-]+ discussion\]\s*"),                       # [#channel discussion]
    re.compile(r"^\[Blog Post\] Document:\s*[^\n]*\n?"),                # [Blog Post] Document: title
    re.compile(r"\b\d+\s+\d\d:\d\d:\d\d[,.]\d{3}\s*-->\s*\d\d:\d\d:\d\d[,.]\d{3}\s*"),  # VTT cue lines
    re.compile(r"\{\d\d:\d\d:\d\d\}\s*"),                            # {00:18:30}
    re.compile(r"\[Founders Member \d+\]\s*"),
]


def clean_excerpt(text: str, limit: int = 280) -> str:
    for rx in NOISE:
        text = rx.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def aggregate(topic: dict, classified: list[dict], jev_mode: str) -> dict:
    relevant = [c for c in classified if c["stance"] != "unrelated"]
    counts = {k: 0 for k in STANCE if k != "unrelated"}
    weighted = {k: 0.0 for k in counts}
    for c in relevant:
        counts[c["stance"]] += 1
        weighted[c["stance"]] += float(c.get("stance_confidence") or 0)
    n = len(relevant)
    share = {k: (counts[k] / n if n else 0.0) for k in counts}
    lead = max(share, key=share.get) if n else "none"
    lead_conf = (weighted[lead] / counts[lead]) if n and counts[lead] else 0.0

    def rank(c: dict) -> float:
        return (float(c.get("stance_confidence") or 0) + float(c.get("actionable") or 0)
                + 0.5 * float(c.get("firsthand") or 0) + (c.get("relevance") or 0))

    sources, seen = [], set()
    for c in sorted(relevant, key=rank, reverse=True):
        excerpt = clean_excerpt(c["text"])
        key = excerpt[:120].lower()
        if key in seen:
            continue  # same chunk indexed under two documents
        seen.add(key)
        sources.append({"source": c["source"], "source_type": c["source_type"], "author": c["author"],
                        "date": c["date"], "link": c["link"], "stance": c["stance"], "excerpt": excerpt})
        if len(sources) == 3:
            break

    conds = [c["condition"] for c in relevant if c.get("condition") and c["condition"] != "none"
             and c["stance"] in ("depends", "cautions")]
    factor = max(set(conds), key=conds.count) if conds else None
    factor_share = round(conds.count(factor) / len(conds), 3) if conds else 0.0
    FACTOR_TEXT = {"spend_level": "daily spend level", "account_size": "account maturity and pixel data",
                   "creative": "creative volume and quality", "product_or_offer": "the product, price, or offer",
                   "seasonality": "timing and seasonality"}

    if n == 0:
        summary = f"No community discussion found yet on: {topic['label'].lower()}."
        verdict = "unknown"
    else:
        s, cau, dep = counts["supports"], counts["cautions"], counts["depends"]
        summary = (f"{n} relevant community sources. {s} support, {cau} caution, {dep} say it depends. "
                   f"Statement tested: \"{topic['question']}\".")
        if share[lead] >= 0.6:
            verdict = lead
        else:
            verdict = "mixed"
    return {
        "id": topic["id"],
        "label": topic["label"],
        "statement": topic["question"],
        "query": topic["query"],
        "n_chunks": len(classified),
        "n_relevant": n,
        "counts": counts,
        "share": {k: round(v, 3) for k, v in share.items()},
        "lead": lead,
        "lead_confidence": round(lead_conf, 3),
        "verdict": verdict,
        "deciding_factor": factor,
        "deciding_factor_share": factor_share,
        "summary": summary,
        "sources": sources,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "jev_mode": jev_mode,
    }


SEARCH_GAP_S = 6.5      # Foxwell MCP allows 10 searches per minute per token
RATE_LIMIT_WAIT_S = 65


async def search_with_backoff(fox: FoxwellClient, query: str):
    for attempt in range(3):
        res = await fox.search(query)
        if res.error != "rate_limited":
            return res
        print(f"  rate limited, waiting {RATE_LIMIT_WAIT_S}s (attempt {attempt + 1}/3)", file=sys.stderr)
        await asyncio.sleep(RATE_LIMIT_WAIT_S)
    return res


async def build(topic_ids: list[str], dry_run: bool, from_raw: bool) -> dict:
    RAW.mkdir(parents=True, exist_ok=True)
    jev = JevClient()
    fox = None if from_raw else FoxwellClient()
    playbook = json.loads(PLAYBOOK.read_text()) if PLAYBOOK.exists() else {}

    for tid in topic_ids:
        topic = TOPIC_BY_ID[tid]
        if not topic["query"]:
            continue
        queries = topic.get("queries") or [topic["query"]]
        chunks, seen = [], set()
        conf = "none"
        for qi, q in enumerate(queries):
            raw_path = RAW / (f"{tid}.json" if qi == 0 else f"{tid}__{qi}.json")
            if from_raw:
                if not raw_path.exists():
                    continue
                res = parse_search_text(q, json.loads(raw_path.read_text())["raw"])
            else:
                assert fox is not None
                res = await search_with_backoff(fox, q)
                if res.error:
                    print(f"[{tid}] query {qi} skipped: {res.error}", file=sys.stderr)
                    continue
                raw_path.write_text(json.dumps({"query": q, "raw": res.raw}, indent=2))
                await asyncio.sleep(SEARCH_GAP_S)
            if res.confidence != "none":
                conf = res.confidence
            for c in res.chunks:
                key = c.text[:160].lower()
                if key in seen:
                    continue
                seen.add(key)
                chunks.append(c)

        class _R:  # keep the print below unchanged
            pass
        res = _R(); res.chunks = chunks; res.confidence = conf
        print(f"[{tid}] {len(res.chunks)} chunks (search confidence {res.confidence})", file=sys.stderr)
        if dry_run:
            continue
        classified = await classify_chunks(jev, topic, res.chunks)
        entry = aggregate(topic, classified, jev.mode)
        entry["chunks"] = classified
        playbook[tid] = entry
        print(f"[{tid}] verdict={entry['verdict']} counts={entry['counts']}", file=sys.stderr)
        PLAYBOOK.write_text(json.dumps(playbook, indent=2))

    await jev.aclose()
    return playbook


def main() -> None:
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--topics", default="", help="comma separated topic ids; default all")
    ap.add_argument("--dry-run", action="store_true", help="search only, skip Jev")
    ap.add_argument("--from-raw", action="store_true", help="reuse data/raw/*.json, skip Foxwell")
    args = ap.parse_args()
    ids = [t.strip() for t in args.topics.split(",") if t.strip()] or [t["id"] for t in TOPICS]
    bad = [i for i in ids if i not in TOPIC_BY_ID]
    if bad:
        sys.exit(f"unknown topics: {bad}")
    if not args.from_raw and not os.environ.get("FOXWELL_MCP_TOKEN"):
        sys.exit("FOXWELL_MCP_TOKEN not set (copy .env.example to .env)")
    asyncio.run(build(ids, args.dry_run, args.from_raw))


if __name__ == "__main__":
    main()
