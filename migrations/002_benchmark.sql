-- FinanceBench open subset: documents and questions, loaded verbatim from the pinned release.
CREATE TABLE benchmark_docs (
    doc_name    text PRIMARY KEY,
    company     text NOT NULL,
    gics_sector text,
    doc_type    text NOT NULL,
    doc_period  int  NOT NULL,
    doc_link    text
);

CREATE TABLE questions (
    id                 text PRIMARY KEY,           -- financebench_id
    benchmark          text NOT NULL DEFAULT 'financebench-open',
    company            text NOT NULL,
    doc_name           text NOT NULL REFERENCES benchmark_docs (doc_name),
    question_type      text NOT NULL,
    question_reasoning text,
    question           text NOT NULL,
    answer             text NOT NULL,
    justification      text,
    evidence           jsonb NOT NULL              -- [{evidence_text, doc_name, evidence_page_num, ...}]
);
CREATE INDEX questions_doc_name_idx ON questions (doc_name);
