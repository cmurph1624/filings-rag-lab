-- Corpus sets: which filings a run searches. Chunk tables cover every document; a run's corpus
-- filters them, so adding filings appends chunks and never changes a pipeline version.
-- Membership is by accession, so it holds across extractor versions.

-- Distractor filings get FinanceBench-style names too (excerpts look the same to the model),
-- so doc_name no longer has to be a benchmark document. Join benchmark_docs to tell them apart.
ALTER TABLE documents DROP CONSTRAINT documents_doc_name_fkey;
COMMENT ON COLUMN documents.doc_name IS
    'FinanceBench doc_name for question documents; a FinanceBench-style name for distractors';

-- A (name, version) is immutable once defined: changing its filings means a new version.
CREATE TABLE corpora (
    name        text NOT NULL,
    version     int  NOT NULL,
    description text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (name, version)
);

CREATE TABLE corpus_documents (
    corpus    text NOT NULL,
    version   int  NOT NULL,
    accession text NOT NULL,
    PRIMARY KEY (corpus, version, accession),
    FOREIGN KEY (corpus, version) REFERENCES corpora (name, version) ON DELETE CASCADE
);
