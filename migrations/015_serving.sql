-- P4: the serving app's tables. In production they live in their own database (frl_app,
-- backed up); the index tables are restored from a dump into another. Applied everywhere so
-- every database has one migration sequence; on the Nucbox they hold only local test queries.

-- One row per /ask request, whatever its outcome. `cost_usd` is the API-equivalent total
-- (generation at plain API price + query embedding + rerank), the figure the daily cap sums.
CREATE TABLE served_queries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    question text NOT NULL,
    status text NOT NULL CHECK (status IN ('reserved', 'answered', 'refused', 'error')),
    refusal text,                      -- why no answer was generated (e.g. out_of_coverage)
    error text,
    answer text,
    model text,
    stop_reason text,
    input_tokens integer NOT NULL DEFAULT 0,
    output_tokens integer NOT NULL DEFAULT 0,
    cache_read_tokens integer NOT NULL DEFAULT 0,
    cache_write_tokens integer NOT NULL DEFAULT 0,
    embed_tokens integer NOT NULL DEFAULT 0,
    gen_cost_usd double precision NOT NULL DEFAULT 0,
    embed_cost_usd double precision NOT NULL DEFAULT 0,
    rerank_cost_usd double precision NOT NULL DEFAULT 0,
    cost_usd double precision NOT NULL DEFAULT 0,
    retrieve_s double precision,
    generate_s double precision,
    retrieved jsonb,                   -- [{chunk_id, doc_name, page, score, section}]
    scope jsonb,
    route jsonb,
    config_hash text NOT NULL,
    git_sha text NOT NULL,
    ip_hash text,                      -- salted sha256; raw addresses are never stored
    tags text[] NOT NULL DEFAULT '{}', -- e.g. {prod} or {smoke}
    trace_id text
);
CREATE INDEX served_queries_created_idx ON served_queries (created_at);

-- Thumbs up/down on an answer; the latest rating per query wins.
CREATE TABLE feedback (
    query_id uuid PRIMARY KEY REFERENCES served_queries (id) ON DELETE CASCADE,
    rating text NOT NULL CHECK (rating IN ('up', 'down')),
    comment text,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Requests per client, for the per-IP rate limit.
CREATE TABLE rate_events (
    ip_hash text NOT NULL,
    at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX rate_events_ip_at_idx ON rate_events (ip_hash, at);
