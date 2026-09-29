"""Parse the metric strings the extension reads off the Ads Manager page.

"$1,234.56" -> 1234.56, "2.31" -> 2.31, "—" -> None, "— Per Purchase" -> None,
"$349.00 Daily" -> 349.0 (budget), "12,345" -> 12345.
"""

from __future__ import annotations

import re

KEYS = {
    "amount spent": "spend",
    "purchases": "purchases",
    "website purchase": "purchases",
    "website purchases": "purchases",
    "purchase roas (return on ad spend)": "roas",
    "purchase roas": "roas",
    "roas": "roas",
    "cost per result": "cpa",
    "cost per purchase": "cpa",
    "results": "results",
    "frequency": "frequency",
    "reach": "reach",
    "impressions": "impressions",
    "link clicks": "link_clicks",
    "adds to cart": "add_to_cart",
    "landing page views": "lpv",
    "budget": "budget",
    "delivery": "delivery",
    "cpm (cost per 1,000 impressions)": "cpm",
    "ctr (link click-through rate)": "ctr",
}

NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def num(s) -> float | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    m = NUM.search(str(s).replace("—", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def normalize(raw: dict) -> dict:
    """Map page labels to stable keys and parse numbers. Text fields (delivery) stay text."""
    out: dict = {}
    for label, value in (raw or {}).items():
        key = KEYS.get(str(label).strip().lower())
        if not key:
            continue
        if key == "delivery":
            out[key] = str(value).strip()
            v = str(value).lower()
            out["in_learning"] = "learning" in v
            out["active"] = v.startswith("active") or "learning" in v
        else:
            n = num(value)
            if n is not None:
                out[key] = n
    return out


def delta(before: dict, after: dict) -> dict:
    d = {}
    for k in ("spend", "purchases", "roas", "cpa", "frequency", "results"):
        b, a = before.get(k), after.get(k)
        if isinstance(b, (int, float)) and isinstance(a, (int, float)):
            d[k] = {"before": b, "after": a, "change_pct": round((a - b) / b * 100, 1) if b else None}
    return d
