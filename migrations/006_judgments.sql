-- Judge verdicts on run answers. `judge` names backend, model and judge prompt, so a different
-- judge gets its own rows and never silently moves another judge's scores.
CREATE TABLE judgments (
    run_id                bigint NOT NULL,
    question_id           text   NOT NULL,
    judge                 text   NOT NULL,       -- e.g. claude-cli:claude-opus-5-5:judge@1
    verdict               text CHECK (verdict IN ('correct', 'incorrect', 'refused')),
    rationale             text,
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
    error                 text,                   -- verdict is NULL; retried on the next judge pass
    judged_at             timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, question_id, judge),
    FOREIGN KEY (run_id, question_id) REFERENCES run_questions (run_id, question_id)
        ON DELETE CASCADE
);
