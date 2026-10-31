# P2 XBRL facts: load and coverage

**FinanceBench open subset, 150 questions. These are not full-set FinanceBench results.**

## What was loaded

- **Source:** `frl facts load` reads SEC `companyfacts` JSON for the 32 companies in
  `corpus/companies.csv`. Taxonomies kept: `us-gaap`, `dei`, `srt`. The load writes
  **885,461 facts** into `xbrl_facts` (migration `010_xbrl_facts.sql`).
- **Rows:** one per fact per filing that reported it, so a filing's own as-filed values are
  selected by accession and later restatements are ignored.
- **Reloads:** each company's rows are replaced in one transaction, so a reload is idempotent.
  The JSON was already in the EDGAR cache from P1, so this made no new SEC requests.
- **Limit:** `companyfacts` has no dimensional facts (segments, regions, product categories).
  Those questions stay on the prose route, and the route labels already put them there.

## Concept map (`config/concepts.toml`, `concepts@1`)

- **Items:** 23 statement items, each an ordered list of us-gaap concepts (companies tag the
  same line differently) and a kind: instant for balances, duration for flows.
- **Metrics:** 28 derived metrics, each with the items it needs.
- **Validation:** every metric name in `routes@1` must resolve.
- **Pulled forward:** the plan put the concept map in PR 4, but the coverage check needs it, so
  it lands here. PR 4 adds the formulas.

## Coverage (`frl facts coverage`, full table in [`p2-xbrl-coverage.md`](p2-xbrl-coverage.md))

For each of the 80 fact and mixed questions, the check asks two things:

- **The data:** is every item its metrics need in `xbrl_facts`, for every period it needs, as
  filed in the question's gold filing?
- **The periods:** a FinanceBench label ("FY2022", "Q2 FY2023") maps to a period-end date through
  the company's filing with that label. Distractors take the `label_offset`, as in scope
  `fiscal_labels = "financebench"`. A fact matches within 10 days of that end, with a year or
  quarter duration for flows.

Results:

| | questions |
|---|---|
| every input in the gold filing, as filed | **79 of 80 (99%)** |
| missing | 1: 01487, whose gold document is J&J's Q2 2023 earnings release (an 8-K with no XBRL) |

The plan's go/no-go bar was about 80%, so **inline XBRL (open decision 1's alternative) isn't
needed.**

**Values were checked, not just presence** (`frl facts coverage --verify`, which reproduces this
and lists the unquoted values). For 247 matched (item, period) values, the value as
statements print it ($M or $K, with commas) was looked for in the question's FinanceBench
evidence text and justification:

- **243 of 247 appear verbatim.**
- **The other 4 are correct but not quoted.** Microsoft's `LongTermDebt` (00552) equals the gold's
  current + long-term sum (5,247 + 41,990 = 47,237; FY2022 2,749 + 47,032 = 49,781). J&J's
  revenue (00956) is its reported $94,943M / $93,775M, which the evidence for this qualitative
  question doesn't quote.

**One concept-map fix came from the check.** Nike tags inventory as
`InventoryFinishedGoodsNetOfReserves`, so it was added to `inventory`'s candidates. That made the
map `concepts@1` as committed; there was no earlier version in use.

## What this means for the fact route

- **Every fact question but one can be answered from its own filing's XBRL.** The earnings-release
  question (01487) would need the matching 10-Q, which isn't in the corpus.
- **A present value isn't proof of the right formula.** The metric definitions in PR 4 decide
  whether the computed number matches the gold (e.g. quick ratio, working capital).
