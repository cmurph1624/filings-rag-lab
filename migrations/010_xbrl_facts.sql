-- XBRL facts (P2 PR 3) from SEC `companyfacts` JSON: one row per fact per filing that reported
-- it, so a filing's own (as-filed) values can be selected by accession, not later restatements.
-- companyfacts has no dimensional facts: segment, region and category figures stay on the prose
-- route. Facts join filings on `documents.accession`.
CREATE TABLE xbrl_facts (
    cik          integer NOT NULL,
    accession    text    NOT NULL,              -- 0000764478-19-000009
    taxonomy     text    NOT NULL,              -- us-gaap, dei, srt
    concept      text    NOT NULL,              -- InventoryNet
    unit         text    NOT NULL,              -- USD, shares, USD/shares, pure
    value        numeric NOT NULL,
    period_start date,                          -- null for instant facts (balances)
    period_end   date    NOT NULL,
    fy           integer,                       -- the filing's fiscal year, not the fact's
    fp           text,                          -- FY, Q1..Q4: the filing's period, likewise
    form         text,
    filed        date,
    frame        text,                          -- SEC's calendar frame, when it assigned one
    UNIQUE NULLS NOT DISTINCT (cik, accession, taxonomy, concept, unit, period_start, period_end)
);
CREATE INDEX xbrl_facts_accession ON xbrl_facts (accession, concept);
CREATE INDEX xbrl_facts_concept ON xbrl_facts (cik, concept, period_end);
