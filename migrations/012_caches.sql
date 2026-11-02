-- Caches for the paid, non-deterministic calls (P3 PR 2).
-- embedding_cache: a new pipeline version re-embeds only chunks whose text changed, and a
-- query embeds once. input_type is in the key because Voyage embeds queries and documents
-- differently. tokens is what the vector cost when it was made (shared out by length when it
-- came from a batch), so a cache hit can be reported as cached instead of re-priced.
CREATE TABLE embedding_cache (
    embedder    text  NOT NULL,
    dims        int   NOT NULL,
    input_type  text  NOT NULL,
    text_sha256 bytea NOT NULL,
    embedding   vector NOT NULL,
    tokens      int   NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (embedder, dims, input_type, text_sha256)
);

-- rerank_cache: Voyage rerank returns scores at varying precision, so near-ties reorder from
-- call to call (docs/results/p3-noise.md). Keyed on the query and the candidate texts in order.
CREATE TABLE rerank_cache (
    model            text  NOT NULL,
    query_sha256     bytea NOT NULL,
    documents_sha256 bytea NOT NULL,
    top_k            int   NOT NULL,
    result           jsonb NOT NULL,   -- [[index, relevance], ...], best first
    tokens           int   NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (model, query_sha256, documents_sha256, top_k)
);

ALTER TABLE pipelines ADD COLUMN embed_cached_tokens bigint NOT NULL DEFAULT 0;
