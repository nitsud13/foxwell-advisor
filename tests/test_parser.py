from advisor.foxwell_client import parse_search_text

SAMPLE = """Found 2 results (confidence: high):

[Source: #scaling | Author: Jane Doe | Date: 2026-05-02 | Age: 4mo ago | Relevance: 61% | Link: https://foxwell.slack.com/archives/C1/p1]
Bump by 20% a day, more than that and you reset learning.

---

[Source: SOP Database — Scaling Playbook | Author: Andrew | Date: 2026-03-10 | Age: 6mo ago | AGING | Relevance: 58% | Link: https://drive.google.com/x]
Step budgets. Above $1k/day the reset matters less."""


def test_parse_blocks():
    res = parse_search_text("q", SAMPLE)
    assert res.count == 2 and res.confidence == "high"
    assert len(res.chunks) == 2
    a, b = res.chunks
    assert a.source == "#scaling" and a.source_type == "slack"
    assert a.author == "Jane Doe" and a.date == "2026-05-02"
    assert a.relevance == 0.61 and a.link.startswith("https://foxwell.slack.com")
    assert a.text.startswith("Bump by 20%")
    assert b.source_type == "gdrive" and b.freshness == "AGING"


def test_parse_empty():
    res = parse_search_text("q", "No relevant community discussions found for this query. Try rephrasing.")
    assert res.chunks == [] and res.count == 0


def test_parse_rate_limit():
    res = parse_search_text("q", "Rate limit exceeded: 10 requests per minute. Please wait.")
    assert res.error == "rate_limited" and res.chunks == []


def test_clean_excerpt():
    from advisor.playbook_build import clean_excerpt
    t = "[#q4-bfcm discussion] Q: hi 886 00:34:58,239 --> 00:35:00,599 there {00:18:30} [Founders Member 1] ok"
    assert clean_excerpt(t) == "Q: hi there ok"
