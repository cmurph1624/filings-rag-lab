-- One row per fetched filing. Raw files stay on disk under data/raw/; extracted text lives here.
CREATE TABLE documents (
    id            serial PRIMARY KEY,
    accession     text NOT NULL UNIQUE,
    doc_name      text UNIQUE REFERENCES benchmark_docs (doc_name),  -- null for non-benchmark filings
    cik           int  NOT NULL,
    ticker        text,
    company       text NOT NULL,
    form_type     text NOT NULL,          -- EDGAR form of the filing: 10-K, 10-Q, 8-K
    fiscal_year   int,                    -- company's own XBRL labels (not FinanceBench's); null for 8-K
    fiscal_period text,                   -- FY | Q1..Q4
    period_end    date,
    filing_date   date NOT NULL,
    source        text NOT NULL CHECK (source IN ('edgar', 'fb_pdf')),
    raw_dir       text NOT NULL,          -- relative to data/
    parts         jsonb NOT NULL,         -- [{type, filename, start, end}] offsets into body
    page_offsets  int[] NOT NULL,         -- char offset where each page starts (page 1 = offset 0)
    body          text NOT NULL,
    extractor     text NOT NULL,          -- extractor name@version that produced body
    fetched_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX documents_scope_idx ON documents (cik, form_type, fiscal_year, fiscal_period);
