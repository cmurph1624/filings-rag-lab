import json
from pathlib import Path

import pytest

from filings_rag.benchmark import synth
from filings_rag.facts import metrics as M
from filings_rag.route.evaluate import fact_matches_gold, gold_number
from filings_rag.route.plan import rules_plan

NAMES = ["Lockheed Martin", "Lockheed"]


@pytest.mark.parametrize("metric", sorted(synth.FACT_METRICS))
def test_every_fact_template_routes_to_its_metric(metric: str) -> None:
    unit = M.REGISTRY[metric].unit if metric in M.REGISTRY else "usd"
    plan = rules_plan(synth.question_text("Lockheed Martin", 2021, metric, unit))
    assert plan.route == "fact"
    assert plan.metrics == (metric,)
    assert (2021, None) in plan.periods


def test_fact_metrics_have_a_unit_hint() -> None:
    for metric in synth.FACT_METRICS:
        if metric in M.REGISTRY:
            assert M.REGISTRY[metric].unit in synth.UNIT_HINT


@pytest.mark.parametrize(
    ("unit", "value", "gold"),
    [
        ("usd", 1_234_400_000.0, "1234"),
        ("usd", -35_600_000.0, "-36"),
        ("pct", 0.13456, "13.5%"),
        ("ratio", 2.3149, "2.31"),
        ("days", 45.678, "45.68"),
        ("ratio", 0.001, None),
        ("usd", 100_000.0, None),
        ("pct", 0.00001, None),
    ],
)
def test_gold_text(unit: str, value: float, gold: str | None) -> None:
    assert synth.gold_text(unit, value) == gold


@pytest.mark.parametrize(
    ("unit", "value"),
    [("usd", 1_234_400_000.0), ("usd", -35_600_000.0), ("pct", 0.13456), ("ratio", 2.3149)],
)
def test_gold_is_matched_by_the_judge_free_check(unit: str, value: float) -> None:
    gold = gold_number(synth.gold_text(unit, value) or "")
    assert gold is not None
    assert fact_matches_gold(value, unit if unit != "ratio" else "x", gold)


def cand(i: int, company: str = "A", metric: str = "roa") -> synth.FactCandidate:
    return synth.FactCandidate(
        company, f"{company}_{2000 + i}_10K", 2000 + i, metric, "pct", 0.1, ""
    )


def test_select_facts_is_seeded_and_capped() -> None:
    metrics = ("roa", "ebitda_margin", "fcf")
    pool = [cand(i, c, m) for i in range(9) for c in "ABCD" for m in metrics]
    a = synth.select_facts(pool, 20, per_company=6, per_metric=8)
    assert a == synth.select_facts(list(reversed(pool)), 20, per_company=6, per_metric=8)
    assert len(a) == 20
    assert max(sum(c.company == k for c in a) for k in "ABCD") <= 6
    assert max(sum(c.metric == k for c in a) for k in metrics) <= 8
    assert a == sorted(a, key=lambda c: (c.doc_name, c.metric))


def test_fact_question_carries_gold_and_formula() -> None:
    c = synth.FactCandidate(
        "3M", "3M_2018_10K", 2018, "roa", "pct", 0.1512, "net_income 5 / assets 33"
    )
    q = synth.fact_question(c)
    assert q.benchmark == synth.FACTS_SET
    assert q.answer == "15.1%"
    assert q.evidence == []
    assert "3M_2018_10K" in (q.justification or "")
    assert q.question.startswith("What is the FY2018 return on assets for 3M")


def test_label_year_uses_the_offset_only_off_the_benchmark() -> None:
    assert synth.label_year("ULTA_2021_10K", 1, set()) == 2022
    assert synth.label_year("ULTA_2021_10K", 1, {"ULTA_2021_10K"}) == 2021


def w(i: int) -> str:
    return "qz" + chr(97 + i // 26 % 26) + chr(97 + i % 26)  # letters only: no digit share


TEXT = " ".join(w(i) for i in range(150))


def test_is_prose_chunk() -> None:
    assert synth.is_prose_chunk("ITEM 7. Management's Discussion", TEXT)
    assert synth.is_prose_chunk("Item 1A. Risk Factors › Part I", TEXT)
    assert not synth.is_prose_chunk("Item 8. Financial Statements", TEXT)
    assert not synth.is_prose_chunk("UNITED STATES", TEXT)
    assert not synth.is_prose_chunk(None, TEXT)
    assert not synth.is_prose_chunk("Item 7", "too short")
    assert not synth.is_prose_chunk("Item 7", " ".join(["1,234.5"] * 150))


def row(i: int, doc: str = "A_2020_10K") -> tuple[object, ...]:
    return (i, i, doc, 1, "A Corp", 3, "Item 7. MD&A", TEXT)


def test_prose_candidates_cap_per_filing_and_are_seeded() -> None:
    rows = [row(i, f"D{i % 4}_2020_10K") for i in range(40)]
    a = synth.prose_candidates(rows, {}, set(), 30, per_doc=5)
    assert len(a) == 20
    assert max(sum(c.doc_name == f"D{k}_2020_10K" for c in a) for k in range(4)) == 5
    assert a == synth.prose_candidates(list(reversed(rows)), {}, set(), 30, per_doc=5)


def one() -> synth.ProseCandidate:
    return synth.prose_candidates([row(7)], {}, set(), 1)[0]


def reply(question: str, answer: str = "Because of X.") -> str:
    return json.dumps({"question": question, "answer": answer})


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("not json", "no JSON"),
        ('{"skip": "boilerplate"}', "model skipped"),
        (reply("Why did A Corp grow in FY2020?", ""), "missing question or answer"),
        (reply("Why did A Corp grow in FY2020"), "not a question of 8-60 words"),
        (reply("Why?"), "not a question of 8-60 words"),
        (reply("Why did the company grow in FY2020 so quickly?"), "does not name the company"),
        (reply("Why did A Corp grow so quickly during the year?"), "does not name exactly"),
        (reply("Why did A Corp grow in FY2020 and FY2019?"), "does not name exactly"),
        (reply("What does the excerpt say A Corp did in FY2020?"), "refers to the excerpt"),
        (
            reply(f"Why did A Corp say {' '.join(w(i) for i in range(10, 19))} in FY2020?"),
            "copies",
        ),
    ],
)
def test_check_prose_drops_bad_generations(text: str, why: str) -> None:
    ok, reason = synth.check_prose(text, one(), ["A Corp"])
    assert ok is None
    assert why in reason


def test_check_prose_keeps_a_good_one() -> None:
    ok, _ = synth.check_prose(
        'noise {"question": "Why did A Corp expand its warehouse network in FY2020?", '
        '"answer": "To cut delivery times."} noise',
        one(),
        ["A Corp"],
    )
    assert ok == (
        "Why did A Corp expand its warehouse network in FY2020?",
        "To cut delivery times.",
    )


def test_copied_run() -> None:
    assert synth.copied_run(f"what is {' '.join(w(i) for i in range(3, 8))} here", TEXT) == 5
    assert synth.copied_run("nothing shared at all in this question", TEXT) == 0


def test_prose_question_evidence_is_the_chunk() -> None:
    c = one()
    q = synth.prose_question(c, "Why did A Corp expand in FY2020?", "Because.")
    assert q.benchmark == synth.PROSE_SET
    assert q.evidence == [{"evidence_text": TEXT, "doc_name": "A_2020_10K"}]
    assert q.id == "synthprose1/A_2020_10K/7"


def test_jsonl_round_trip(tmp_path: Path) -> None:
    qs = [synth.prose_question(one(), "Why did A Corp expand in FY2020?", "Because.")]
    path = tmp_path / "s.jsonl"
    assert synth.write_jsonl(path, qs) == 1
    assert synth.read_jsonl(path) == qs
