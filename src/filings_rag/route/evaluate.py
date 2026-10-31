"""Score a router against the hand route labels (`routes@1`), without any generation."""

from collections import Counter
from dataclasses import dataclass

from filings_rag.eval.routes import RouteLabel
from filings_rag.facts.lookup import parse_period
from filings_rag.route.plan import RoutePlan


@dataclass(frozen=True)
class RouteScore:
    question_id: str
    label: RouteLabel
    plan: RoutePlan

    @property
    def route_ok(self) -> bool:
        return self.plan.route == self.label.route

    @property
    def metrics_ok(self) -> bool:
        """Same metric set; only scored where both sides use facts."""
        return set(self.plan.metrics) == set(self.label.metrics)

    @property
    def periods_ok(self) -> bool:
        return set(self.plan.periods) == {parse_period(p) for p in self.label.periods}


def summarize(scores: list[RouteScore]) -> str:
    n = len(scores)
    confusion = Counter((s.label.route, s.plan.route) for s in scores)
    factual = [s for s in scores if s.label.route != "prose" and s.plan.route != "prose"]
    lines = [
        f"route accuracy: {sum(s.route_ok for s in scores)}/{n}",
        f"metrics exact (both non-prose): {sum(s.metrics_ok for s in factual)}/{len(factual)}",
        f"periods exact: {sum(s.periods_ok for s in scores)}/{n}",
        "",
        "label -> router: " + ", ".join(f"{a}->{b} {k}" for (a, b), k in sorted(confusion.items())),
        "",
        "disagreements:",
    ]
    for s in scores:
        bad = [
            w
            for w, ok in (
                ("route", s.route_ok),
                ("metrics", s.metrics_ok or s.label.route == "prose" or s.plan.route == "prose"),
                ("periods", s.periods_ok),
            )
            if not ok
        ]
        if bad:
            lines.append(
                f"  {s.question_id[16:]} {'/'.join(bad)}: label {s.label.route}"
                f" {','.join(s.label.metrics) or '-'} {';'.join(s.label.periods)}"
                f" | plan {s.plan.route} {','.join(s.plan.metrics) or '-'}"
                f" {';'.join(s.plan.to_json()['periods'])}"
            )
    return "\n".join(lines)
