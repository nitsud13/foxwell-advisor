import pytest

from advisor.jev_client import JevClient, choice, noul, score


@pytest.fixture
def jev(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    return JevClient(api_key="")


async def test_stub_choice_picks_overlap(jev):
    r = await jev.ask({"description": "daily_budget changed from 200 to 600"},
                      {"t": choice("which", {"budget": "raising a daily budget", "bid": "changing bid strategy"})})
    a = r["answers"]["t"]
    assert a["choice"] == "budget" and abs(sum(a["probabilities"].values()) - 1) < 1e-3
    assert r["mode"] == "stub"


async def test_stub_score_uses_delta_hint(jev):
    big = await jev.ask({"delta_pct": 200}, {"r": score("risk", ["none", "low", "medium", "high", "severe"])})
    small = await jev.ask({"delta_pct": 10}, {"r": score("risk", ["none", "low", "medium", "high", "severe"])})
    assert big["answers"]["r"]["score"] > small["answers"]["r"]["score"]


async def test_stub_noul_range(jev):
    r = await jev.ask("hello", {"n": noul("is it", true="hello world", false="bye")})
    assert 0 <= r["answers"]["n"]["noul"] <= 1


def test_validation():
    with pytest.raises(ValueError):
        choice("x", {"only": "one"})
    with pytest.raises(ValueError):
        score("x", ["one"])
