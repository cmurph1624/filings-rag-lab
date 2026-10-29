-- structured@1 (P1 PR 7) records each chunk's section ("Item 8. Financial Statements … ›
-- Consolidated Balance Sheets"). Chunk tables are created per pipeline by `frl ingest build`,
-- whose DDL now includes the column; this adds it (nullable, so no output changes) to the
-- tables built before.
DO $$
DECLARE t text;
BEGIN
    FOR t IN SELECT chunk_table FROM pipelines LOOP
        IF to_regclass(quote_ident(t)) IS NOT NULL THEN
            EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS section text', t);
        END IF;
    END LOOP;
END $$;
