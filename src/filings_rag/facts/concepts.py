"""The concept map (`config/concepts.toml`): statement items as ordered us-gaap concepts, and
the items each derived metric needs. Every metric name the route labels use must resolve."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from filings_rag.eval.routes import METRICS as ROUTE_METRICS

KINDS = ("instant", "duration")


@dataclass(frozen=True)
class Item:
    name: str
    kind: str  # instant (a balance at a date) | duration (a flow over a period)
    concepts: tuple[str, ...]


@dataclass(frozen=True)
class ConceptMap:
    version: str
    items: dict[str, Item]
    metrics: dict[str, tuple[str, ...]]

    def inputs(self, name: str) -> tuple[str, ...]:
        """The items a route-label metric needs: an item is its own input."""
        if name in self.items:
            return (name,)
        if name in self.metrics:
            return self.metrics[name]
        raise KeyError(f"{name!r} is neither an item nor a metric in {self.version}")


def load(path: Path) -> ConceptMap:
    raw = tomllib.loads(path.read_text())
    items: dict[str, Item] = {}
    for name, body in raw.get("items", {}).items():
        if body.get("kind") not in KINDS:
            raise ValueError(f"item {name}: kind must be one of {KINDS}")
        concepts = tuple(body.get("concepts") or ())
        if not concepts:
            raise ValueError(f"item {name}: no concepts")
        items[name] = Item(name, body["kind"], concepts)
    metrics = {name: tuple(ins) for name, ins in raw.get("metrics", {}).items()}
    for name, ins in metrics.items():
        if name in items:
            raise ValueError(f"{name} is both an item and a metric")
        if bad := [i for i in ins if i not in items]:
            raise ValueError(f"metric {name}: unknown items {bad}")
    cmap = ConceptMap(str(raw.get("version", "")), items, metrics)
    if missing := sorted(m for m in ROUTE_METRICS if m not in items and m not in metrics):
        raise ValueError(f"{path.name} does not define route-label metrics {missing}")
    return cmap
