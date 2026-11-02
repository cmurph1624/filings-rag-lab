-- Faithfulness (faithful@1): each answer's checkable claims and whether the context the
-- generator was given supports them. Graded against the context, not the gold answer, so it
-- lives beside judgments rather than in it. Same accounting as judgments.
CREATE TABLE faithfulness (
    run_id                bigint NOT NULL,
    question_id           text   NOT NULL,
    judge                 text   NOT NULL,       -- e.g. claude-cli:claude-opus-5-5:faithful@1
    claims                jsonb,                  -- [{claim, support, source}], NULL on error
    supported             int,
    unsupported           int,
    contradicted          int,
    attempts              int    NOT NULL DEFAULT 1,
    model                 text,
    input_tokens          int NOT NULL DEFAULT 0,  -- tokens and cost summed over attempts
    output_tokens         int NOT NULL DEFAULT 0,
    cache_read_tokens     int NOT NULL DEFAULT 0,
    cache_write_tokens    int NOT NULL DEFAULT 0,
    cache_write_1h_tokens int NOT NULL DEFAULT 0,
    cost_usd              double precision NOT NULL DEFAULT 0,
    reported_cost_usd     double precision,
    latency_s             real,
    error                 text,                   -- claims are NULL; retried on the next pass
    judged_at             timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, question_id, judge),
    FOREIGN KEY (run_id, question_id) REFERENCES run_questions (run_id, question_id)
        ON DELETE CASCADE
);
