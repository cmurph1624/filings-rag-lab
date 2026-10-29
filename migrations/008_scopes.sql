-- Query scoping (P1 PR 3). A run row records the scope its retrieval used, and the spend of
-- any model other than the generator (the scope model now; a reranker later) in `aux`:
-- {component: {model, usage, cost_usd, cached}}. Reports fold `aux` into answering cost.
ALTER TABLE run_questions
    ADD COLUMN scope jsonb,                       -- {mode, company, years, quarters, level, docs}
    ADD COLUMN aux   jsonb NOT NULL DEFAULT '{}';

-- llm-mode extractions, cached per (question, backend:model:prompt) so grid runs pay once.
CREATE TABLE query_scopes (
    question_id text NOT NULL REFERENCES questions (id),
    scope_key   text NOT NULL,
    mentions    jsonb NOT NULL,                   -- {company, years, quarters}
    model       text NOT NULL,
    usage       jsonb NOT NULL,                   -- token counts, as in pricing.Usage
    cost_usd    double precision NOT NULL,        -- API-equivalent at list price
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (question_id, scope_key)
);
