# P2 metrics library (`metrics@1`)

**FinanceBench open subset, 150 questions. These are not full-set FinanceBench results.**

`src/filings_rag/facts/metrics.py` computes the 28 derived metrics in `config/concepts.toml`
from XBRL facts, one FinanceBench period at a time. Each result carries:
- **the value and its unit** (usd, ratio, pct as a fraction, days);
- **a formula with the numbers filled in;**
- **the facts used** (concept, accession, and whether it is the anchor filing's own as-filed
  value).

`frl facts get <doc> <metric> <period> [--option k=v]` prints all three. For example:

```
$ frl facts get AMAZON_2017_10K dpo FY2017 --option denominator=purchases
dpo FY2017 (metrics@1): 93.86 days  [93.8578]
  = 365 x average accounts_payable 29,962 / (cogs 111,934 + change in inventory 4,586)
  accounts_payable FY2017: 34,616,000,000 (AccountsPayableCurrent, as filed)
  ...
```

## Definitions (standard textbook, P2 decision 4)

| metric | formula | options (default first) |
|---|---|---|
| working_capital | current assets − current liabilities | |
| current_ratio | current assets / current liabilities | |
| quick_ratio | (cash + short-term investments + receivables) / current liabilities; short-term investments are 0 when none are reported | |
| gross / operating / net / COGS / D&A margin | line / revenue (gross: (revenue − COGS) / revenue) | |
| ebitda, ebitda_margin | operating income + D&A (from the cash-flow statement); / revenue | |
| fcf, fcf_conversion | CFO − capex; / net income | |
| capex_pct_revenue, capital_intensity, fixed_asset_ratio | capex / revenue; total assets / revenue; net PP&E / total assets | |
| roa, asset_turnover, fixed_asset_turnover, inventory_turnover | flow / balance, averaged over the period and the prior one | `average`: true, false |
| dpo | 365 × average payables / COGS | `denominator`: cogs, purchases (COGS + change in inventory) |
| ccc | DIO + DSO − DPO, each over average balances | `denominator` (DPO's) |
| ocf_ratio | CFO / current liabilities | |
| effective_tax_rate | income tax expense / pretax income | |
| payout_ratio, retention_ratio | dividends paid / net income; 1 − that | |
| revenue_growth, operating_income_growth | this period / prior period − 1 | |
| revenue_cagr | (end / start)^(1/years) − 1 | `since` (required) |

Rules:
- **Inputs:** a missing input raises `MissingInput`, naming the item and period. No metric guesses
  a substitute line. The one exception is the quick ratio's optional short-term investments,
  which count as 0 when none are reported.
- **Zero denominators** raise an error, not infinity.
- **Agreement with the concept map:** every metric's required inputs must equal its
  `concepts.toml` entry, and a test checks this.

## Against FinanceBench gold answers

15 questions, computed from the gold filing's own facts. Where a question spells out its formula,
the matching option is used (`tests/test_metrics.py`, which runs when facts are loaded):

| question | metric | gold | metrics@1 |
|---|---|---|---|
| 04417 Best Buy FY2019 inventory | inventory | $5,409M | $5,409M |
| 06655 Amazon FY2017 DPO | dpo (purchases) | 93.86 | 93.86 |
| 10130 Corning FY2020 DPO | dpo (purchases) | 63.86 | 63.86 |
| 06247 Walmart FY2018 DPO | dpo (purchases) | 42.69 | 42.69 |
| 04080 Nike FY2021 inventory turnover | inventory_turnover | 3.46 | 3.46 |
| 10499 Kraft Heinz FY2019 inventory turnover | inventory_turnover | 6.25 | 6.25 |
| 03471 General Mills FY2020 working capital ratio | current_ratio | 0.68 | 0.68 |
| 04412 Lockheed FY2020 asset turnover | asset_turnover | 1.33 | 1.33 |
| 02987 Activision FY2019 fixed asset turnover | fixed_asset_turnover | 24.26 | 24.26 |
| 03856 Adobe FY2017 operating cash flow ratio | ocf_ratio | 0.83 | 0.83 |
| 10136 General Mills FY2022 retention ratio | retention_ratio | 0.54 | 0.54 |
| 03620 PepsiCo FY2022 EBITDA less capex | ebitda − capex | $9,068M | $9,068M |
| 04481 PepsiCo FY2022 EBITDA margin | ebitda_margin | 16.5% | 16.5% |
| 03718 Lockheed FY2020–22 revenue CAGR | revenue_cagr | 0.4% | 0.4% |
| 04103 General Mills FY2019 CCC | ccc (purchases) | −3.7 | −3.7 |

All 15 match to the gold's precision, using only the gold filing's own as-filed facts.

These are spot checks, not a fact-route accuracy number. Choosing the metric, periods and options
from the question is the router's job (PR 5), and PR 7 reports fact-route accuracy on all 80
fact and mixed questions.
