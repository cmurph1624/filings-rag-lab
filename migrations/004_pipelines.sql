-- Ingest pipeline registry. A (name, version) is immutable once built: changing chunking,
-- embedder or source extractor means a new version with its own chunk table.
CREATE TABLE pipelines (
    name        text NOT NULL,
    version     int  NOT NULL,
    params      jsonb NOT NULL,
    embedder    text NOT NULL,
    dims        int  NOT NULL,
    extractor   text NOT NULL,       -- documents.extractor this pipeline reads
    chunk_table text NOT NULL UNIQUE,
    embed_tokens bigint NOT NULL DEFAULT 0,   -- embedding tokens spent building the index
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (name, version)
);

-- Which chunks hold each FinanceBench evidence passage, per pipeline version.
-- chunk_id points into that pipeline's chunk_table (no FK: the table is per-pipeline).
CREATE TABLE evidence_labels (
    pipeline     text NOT NULL,
    version      int  NOT NULL,
    question_id  text NOT NULL REFERENCES questions (id),
    evidence_idx int  NOT NULL,      -- position in questions.evidence
    chunk_id     bigint NOT NULL,
    coverage     real NOT NULL,      -- share of the evidence passage found in this chunk
    PRIMARY KEY (pipeline, version, question_id, evidence_idx, chunk_id),
    FOREIGN KEY (pipeline, version) REFERENCES pipelines (name, version)
);
