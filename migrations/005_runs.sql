-- Experiment runs: one config (pipeline, retriever, generation model, judge) over a question set.
CREATE TABLE runs (
    id          bigserial PRIMARY KEY,
    name        text NOT NULL,
    config      jsonb NOT NULL,          -- the config file as loaded
    config_hash text NOT NULL,           -- over everything that shapes results (not name/execution)
    git_sha     text NOT NULL,           -- at run start; run_questions records it per answer
    git_dirty   boolean NOT NULL,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz              -- set once every question in the set has an answer
);
CREATE INDEX runs_config_hash_idx ON runs (config_hash);

-- One row per question per run. A row with `error` set is retried when the run resumes.
CREATE TABLE run_questions (
    run_id                bigint NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    question_id           text   NOT NULL REFERENCES questions (id),
    git_sha               text   NOT NULL,
    attempts              int    NOT NULL DEFAULT 1,
    retrieved             jsonb  NOT NULL DEFAULT '[]',  -- [{chunk_id, doc_name, page, score}], rank order
    answer                text,
    model                 text,          -- the model that served the answer
    stop_reason           text,
    input_tokens          int NOT NULL DEFAULT 0,
    output_tokens         int NOT NULL DEFAULT 0,
    cache_read_tokens     int NOT NULL DEFAULT 0,
    cache_write_tokens    int NOT NULL DEFAULT 0,
    cache_write_1h_tokens int NOT NULL DEFAULT 0,
    embed_tokens          int NOT NULL DEFAULT 0,   -- query embedding, summed over attempts
    gen_cost_usd          double precision NOT NULL DEFAULT 0,  -- API-equivalent, list price
    embed_cost_usd        double precision NOT NULL DEFAULT 0,  -- summed over attempts
    reported_cost_usd     double precision,          -- what the backend itself reported
    retrieve_s            real,
    generate_s            real,
    error                 text,
    answered_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, question_id)
);
