from collections import Counter
from pathlib import Path

import pytest

from filings_rag.eval import routes
from filings_rag.eval.report import RunSummary, _by_route, subset_accuracy

LABELS = Path(__file__).resolve().parents[1] / "corpus" / "route_labels.csv"


def row(**kw: str) -> dict[str, str]:
    base = {
        "question_id": "q1",
        "route": "fact",
        "metrics": "dpo",
        "periods": "FY2019;FY2020",
        "multi_period": "true",
    }
    return base | kw


def test_committed_labels_load_and_cover_the_open_subset() -> None:
    labels = routes.load(LABELS)
    assert len(labels) == 150
    assert all(q.startswith("financebench_id_") for q in labels)
    assert Counter(lab.route for lab in labels.values()) == {"fact": 63, "mixed": 17, "prose": 70}
    assert sum(lab.multi_period for lab in labels.values()) == 50


def test_parse_row_accepts_quarters_and_splits_lists() -> None:
    lab = routes.parse_row(row(metrics="cfo;cfi", periods="Q2 FY2022;Q2 FY2023"))
    assert lab.metrics == ("cfo", "cfi")
    assert lab.periods == ("Q2 FY2022", "Q2 FY2023")
    assert lab.multi_period


@pytest.mark.parametrize(
    ("kw", "why"),
    [
        ({"route": "sql"}, "unknown route"),
        ({"metrics": "dpo;ebit"}, "unknown metrics"),
        ({"route": "prose"}, "prose takes no metrics"),
        ({"metrics": ""}, "need at least one"),
        ({"periods": "2022"}, "periods must be"),
        ({"periods": "FY2020"}, "multi_period"),
        ({"multi_period": "yes"}, "multi_period"),
    ],
)
def test_parse_row_rejects_bad_rows(kw: dict[str, str], why: str) -> None:
    with pytest.raises(ValueError, match=why):
        routes.parse_row(row(**kw))


def summary(verdicts: dict[str, str], generated: bool = True) -> RunSummary:
    s = RunSummary(1, "r", "h", "g", "p", "c", "vector", "none", "none", "gen", "judge", 3)
    s.generated = generated
    s.verdict_of = verdicts
    return s


def test_subset_accuracy_counts_unjudged_questions_as_not_correct() -> None:
    s = summary({"a": "correct", "b": "refused"})
    assert subset_accuracy(s, ["a", "b", "c"]) == "33.3% (1/3)"


def test_by_route_table_rows_and_retrieval_only_dashes() -> None:
    labels = {
        "a": routes.RouteLabel("a", "fact", ("dpo",), ("FY2019", "FY2020"), True),
        "b": routes.RouteLabel("b", "prose", (), ("FY2022",), False),
    }
    out = "\n".join(
        _by_route([summary({"a": "correct"}), summary({}, False)], ["", "x", "y"], labels)
    )
    assert "| route: fact (1) | 100.0% (1/1) | – |" in out
    assert "| route: mixed (0) | – | – |" in out
    assert "| multi-period (1) | 100.0% (1/1) | – |" in out
