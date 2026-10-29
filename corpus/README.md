# corpus/

Small committed files that make the corpus reproducible. Downloaded filings live in `data/` (gitignored).

- `companies.csv`: FinanceBench company name → ticker → SEC CIK. Activision Blizzard and Foot Locker
  are no longer in SEC's ticker file (both acquired), so their CIKs come from EDGAR directly.
- `overrides.csv`: hand-picked accessions for docs the rules can't settle, each with a reason.
- `manifest.csv`: output of `frl corpus audit`, one row per FinanceBench document. Regenerate it, don't hand-edit it.
  `form`, `fiscal_year` and `fiscal_period` are **FinanceBench's labels**, parsed from `doc_name`.
  `report_date` is the period end that EDGAR reports.

Resolution order: override → accession embedded in the FinanceBench `doc_link` → XBRL fiscal
year/period match (10-K, 10-Q) or report/filing date match (8-K).

Notes:
- FinanceBench's fiscal-year labels don't always follow the company's own. `ULTABEAUTY_2023_10K` is
  Ulta's fiscal 2022 10-K (period ending 2023-01-28), and the questions call it FY2023.
- For the earnings and 8-K docs, the text the questions use is mostly in exhibit 99.1, not the
  primary document. Fetch has to pull exhibits.
