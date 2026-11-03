-- Synthetic question sets (P3 PR 8: synth-facts@1, synth-prose@1) are questions about any
-- fetched filing, not only the FinanceBench documents, so a question's gold filing no longer
-- has to be a benchmark document. The open subset's rows are unchanged; join benchmark_docs to
-- tell the two apart, or filter on questions.benchmark.
ALTER TABLE questions DROP CONSTRAINT questions_doc_name_fkey;
COMMENT ON COLUMN questions.doc_name IS
    'gold filing: a FinanceBench document for the open subset, any fetched filing otherwise';
CREATE INDEX questions_benchmark_idx ON questions (benchmark);
