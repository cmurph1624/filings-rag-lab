-- Routing (P2 PR 5). A run row records the route plan its question got and the facts the fact
-- route computed: {plan: {route, metrics, periods, options, aggregate}, anchor, facts: [...]}.
ALTER TABLE run_questions ADD COLUMN route jsonb;

-- llm-mode route plans, cached per (question, backend:model:prompt) like query_scopes.
CREATE TABLE query_routes (
    question_id text NOT NULL REFERENCES questions (id),
    route_key   text NOT NULL,
    plan        jsonb NOT NULL,                   -- the parsed plan, or {parse_error}
    model       text NOT NULL,
    usage       jsonb NOT NULL,
    cost_usd    double precision NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (question_id, route_key)
);
