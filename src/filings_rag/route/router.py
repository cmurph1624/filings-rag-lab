"""Routers behind one interface, and their config (`[router]` in a run config)."""

from dataclasses import asdict
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from filings_rag.backends.base import ModelBackend
from filings_rag.backends.factory import make_backend
from filings_rag.pricing import model_cost
from filings_rag.route.plan import ROUTE_PROMPTS, RoutePlan, parse_llm_plan, rules_plan

# Every option with its default, so the config hash covers what was used.
ROUTER_DEFAULTS: dict[str, dict[str, Any]] = {
    "rules": {},
    "llm": {"backend": "claude-cli", "model": "claude-haiku-5-5", "prompt": "route@1"},
}


def resolve_router(spec: dict[str, Any] | None) -> dict[str, Any] | None:
    """The [router] table with defaults filled in; None for no router (the P1 behaviour)."""
    if not spec:
        return None
    mode = spec.get("mode")
    if mode not in ROUTER_DEFAULTS:
        raise ValueError(f"unknown router mode {mode!r}; known: {list(ROUTER_DEFAULTS)}")
    opts = {k: v for k, v in spec.items() if k != "mode"}
    if unknown := opts.keys() - ROUTER_DEFAULTS[mode].keys():
        raise ValueError(f"unknown {mode} router options: {sorted(unknown)}")
    resolved = {"mode": mode, **(ROUTER_DEFAULTS[mode] | opts)}
    if mode == "llm" and resolved["prompt"] not in ROUTE_PROMPTS:
        raise ValueError(
            f"unknown route prompt {resolved['prompt']!r}; known: {sorted(ROUTE_PROMPTS)}"
        )
    return resolved


class Router:
    def __init__(
        self,
        conn: psycopg.Connection[Any],
        spec: dict[str, Any],
        backend: ModelBackend | None = None,
    ) -> None:
        self._conn = conn
        self.spec = spec
        self.mode = spec["mode"]
        self._backend = backend
        if self.mode == "llm":
            if backend is None:
                raise ValueError("llm router needs a backend")
            self._prompt = ROUTE_PROMPTS[spec["prompt"]]
            self.key = f"{spec['backend']}:{spec['model']}:{spec['prompt']}"

    def route(self, question_id: str, question: str) -> tuple[RoutePlan, dict[str, Any] | None]:
        """(plan, aux spend or None). A reply that won't parse routes to prose, and is cached
        with its cost and the error so a resume doesn't pay again."""
        if self.mode == "rules":
            return rules_plan(question), None
        row = self._conn.execute(
            "SELECT plan, model, usage, cost_usd FROM query_routes"
            " WHERE question_id = %s AND route_key = %s",
            (question_id, self.key),
        ).fetchone()
        self._conn.commit()
        if row is not None:
            plan, model, usage, cost = row
            hit: dict[str, Any] = {"model": model, "usage": usage, "cost_usd": cost, "cached": True}
            if plan.get("parse_error"):
                hit["parse_error"] = plan["parse_error"]
                return RoutePlan("prose"), hit
            return RoutePlan.from_json(plan), hit
        assert self._backend is not None
        result = self._backend.generate(self._prompt.system, self._prompt.render(question))
        cost = model_cost(result.model, result.usage)
        usage = asdict(result.usage)
        aux: dict[str, Any] = {"model": result.model, "usage": usage, "cost_usd": cost}
        try:
            plan = parse_llm_plan(result.text)
            stored = plan.to_json()
        except (ValueError, TypeError, AttributeError) as e:
            plan = RoutePlan("prose")
            aux["parse_error"] = f"{e}"[:300]
            stored = {"parse_error": aux["parse_error"]}
        self._conn.execute(
            "INSERT INTO query_routes (question_id, route_key, plan, model, usage, cost_usd)"
            " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (question_id, self.key, Jsonb(stored), result.model, Jsonb(usage), cost),
        )
        self._conn.commit()
        return plan, {**aux, "cached": False}


def make_router(conn: psycopg.Connection[Any], spec: dict[str, Any] | None) -> Router | None:
    resolved = resolve_router(spec)
    if resolved is None:
        return None
    backend = None
    if resolved["mode"] == "llm":
        backend = make_backend({"backend": resolved["backend"], "model": resolved["model"]})
    return Router(conn, resolved, backend)
