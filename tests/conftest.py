"""Shared fixtures."""

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest


@dataclass
class Node:
    """One recorded trace step: its fields as last updated, its children, and any error."""

    name: str
    kind: str
    fields: dict[str, Any]
    children: list["Node"] = field(default_factory=list)
    error: BaseException | None = None

    def update(self, **fields: Any) -> None:
        self.fields.update(fields)

    def names(self) -> list[str]:
        return [c.name for c in self.children]

    def find(self, name: str) -> "Node":
        for c in self.children:
            if c.name == name:
                return c
            with contextlib.suppress(KeyError):
                return c.find(name)
        raise KeyError(name)


class RecordingTracer:
    """A `trace.Tracer` that keeps the step tree in memory."""

    enabled = True

    def __init__(self) -> None:
        self.roots: list[Node] = []
        self.scores: list[dict[str, Any]] = []
        self.shut = False
        self._stack: list[Node] = []

    @contextlib.contextmanager
    def _open(self, name: str, kind: str, fields: dict[str, Any]) -> Iterator[Node]:
        node = Node(name, kind, dict(fields))
        (self._stack[-1].children if self._stack else self.roots).append(node)
        self._stack.append(node)
        try:
            yield node
        except BaseException as e:
            node.error = e
            raise
        finally:
            self._stack.pop()

    def trace(self, trace_id: str, name: str, **fields: Any) -> Any:
        return self._open(name, "trace", {"trace_id": trace_id, **fields})

    def observe(self, name: str, *, kind: str = "span", **fields: Any) -> Any:
        return self._open(name, kind, fields)

    def score(self, trace_id: str, name: str, value: float, **fields: Any) -> None:
        self.scores.append({"trace_id": trace_id, "name": name, "value": value, **fields})

    def shutdown(self) -> None:
        self.shut = True


@pytest.fixture
def tracer() -> RecordingTracer:
    return RecordingTracer()
