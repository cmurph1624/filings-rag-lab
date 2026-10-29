# corpus/

Small committed files that make the corpus reproducible. Downloaded filings live in `data/` (gitignored).

- `companies.csv`: FinanceBench company name → ticker → SEC CIK, plus `aliases` (`;`-separated:
  JPM, JnJ, AmEx, Coca Cola, Footlocker, Square...) that rules-based query scoping matches. Activision Blizzard and Foot Locker
  are no longer in SEC's ticker file (both acquired), so their CIKs come from EDGAR directly.
- `overrides.csv`: hand-picked accessions for docs the rules can't settle, each with a reason.
- `manifest.csv`: output of `frl corpus audit`, one row per FinanceBench document. Regenerate it, don't hand-edit it.
- `distractors.csv`: output of `frl corpus expand`, the same companies' other filings: every
  10-K for FY2015–2023 and, for the six companies with 10-Q questions (3M, Amcor, Best Buy,
  JPMorgan, MGM, Pfizer), every 10-Q for FY2021–2024. `note` says why each row is there.
  Regenerate it, don't hand-edit it.
  `form`, `fiscal_year` and `fiscal_period` are **FinanceBench's labels**, parsed from `doc_name`.
  `report_date` is the period end that EDGAR reports.

Resolution order: override → accession embedded in the FinanceBench `doc_link` → XBRL fiscal
year/period match (10-K, 10-Q) or report/filing date match (8-K).

Notes:
- FinanceBench's fiscal-year labels don't always follow the company's own. `ULTABEAUTY_2023_10K` is
  Ulta's fiscal 2022 10-K (period ending 2023-01-28), and the questions call it FY2023.
- For the earnings and 8-K docs, the text the questions use is mostly in exhibit 99.1, not the
  primary document. Fetch has to pull exhibits.

## Corpus sets

A run config names the filings it searches with `corpus = "name@version"` (default `fb-gold@1`).
Definitions live in `src/filings_rag/corpora.py` and are recorded once with `frl corpus define`;
changing a set's filings means a new version.

| corpus | filings |
|---|---|
| `fb-gold@1` | the 84 question filings (`manifest.csv`), what every P0 run searched |
| `fb-distractors@1` | those plus the 283 in `distractors.csv` |

Rebuild from scratch: `frl corpus expand`, `frl corpus fetch --manifest distractors.csv`,
`frl corpus define fb-gold@1`, `frl corpus define fb-distractors@1`, `frl ingest build naive-fixed@1`.

Distractor fiscal labels come from period-end dates, not each filing's XBRL `fy` (Best Buy's
fiscal 2021 10-K says 2020; Costco's 2019 10-K says Q4). XBRL only sets each company's
convention for years ending in January. Two gaps are real: Activision Blizzard was acquired
before filing a FY2023 10-K, and Amcor plc's first 10-K is FY2019. One distractor name was
already taken by a FinanceBench label (Ulta's are a year off), so it carries its period end:
`ULTABEAUTY_2023_10K_2024-02-03`.
