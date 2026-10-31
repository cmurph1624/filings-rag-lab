# XBRL coverage (concepts@1, `companyfacts`)

**FinanceBench open subset. These are not full-set FinanceBench results.**

Fact and mixed questions (`routes@1`): 80.

| coverage | questions |
|---|---|
| every input in the gold filing (as filed) | 79 (99%) |
| every input somewhere, not all in the gold filing | 0 |
| some input missing | 1 |

| question | route | gold filing | metrics | status | missing / not in gold |
|---|---|---|---|---|---|
| 00005 | mixed | CORNING_2022_10K | working_capital | gold | – |
| 00070 | mixed | AMERICANWATERWORKS_2022_10K | working_capital | gold | – |
| 00080 | mixed | PAYPAL_2022_10K | working_capital | gold | – |
| 00215 | mixed | VERIZON_2022_10K | capital_intensity | gold | – |
| 00216 | mixed | VERIZON_2022_10K | quick_ratio | gold | – |
| 00222 | mixed | AMD_2022_10K | quick_ratio | gold | – |
| 00288 | fact | BESTBUY_2024Q2_10Q | cash | gold | – |
| 00302 | fact | PFIZER_2021_10K | ppe_net | gold | – |
| 00438 | mixed | ADOBE_2022_10K | operating_margin | gold | – |
| 00499 | mixed | 3M_2022_10K | capex_pct_revenue, fixed_asset_ratio, roa | gold | – |
| 00540 | mixed | AES_2022_10K | inventory_turnover | gold | – |
| 00552 | fact | MICROSOFT_2023_10K | debt | gold | – |
| 00566 | fact | VERIZON_2022_10K | debt | gold | – |
| 00585 | fact | BOEING_2022_10K | effective_tax_rate | gold | – |
| 00591 | fact | ADOBE_2022_10K | fcf_conversion | gold | – |
| 00678 | mixed | BOEING_2022_10K | gross_margin | gold | – |
| 00684 | mixed | AMCOR_2023_10K | gross_margin | gold | – |
| 00685 | mixed | BESTBUY_2023_10K | gross_margin | gold | – |
| 00711 | mixed | JOHNSON_JOHNSON_2022_10K | inventory_turnover | gold | – |
| 00790 | mixed | CVSHEALTH_2022_10K | fixed_asset_ratio, roa | gold | – |
| 00799 | mixed | AMCOR_2023_10K | quick_ratio | gold | – |
| 00807 | mixed | 3M_2023Q2_10Q | quick_ratio | gold | – |
| 00956 | mixed | JOHNSON_JOHNSON_2022_10K | revenue_growth | gold | – |
| 01163 | fact | NIKE_2023_10K | cfo, cfi, cff | gold | – |
| 01254 | fact | MGMRESORTS_2022_10K | dividends_paid | gold | – |
| 01275 | fact | BESTBUY_2023_10K | cfo, cfi, cff | gold | – |
| 01279 | fact | AMD_2022_10K | cfo, cfi, cff | gold | – |
| 01346 | fact | CORNING_2022_10K | effective_tax_rate | gold | – |
| 01351 | fact | AMERICANEXPRESS_2022_10K | effective_tax_rate | gold | – |
| 01487 | fact | JOHNSON_JOHNSON_2023Q2_EARNINGS (no XBRL) | net_margin | missing | net_income Q2 FY2022; net_income Q2 FY2023; revenue Q2 FY2022; revenue Q2 FY2023 |
| 02608 | fact | BESTBUY_2017_10K | net_margin | gold | – |
| 02981 | fact | CORNING_2021_10K | operating_margin | gold | – |
| 02987 | fact | ACTIVISIONBLIZZARD_2019_10K | fixed_asset_turnover | gold | – |
| 03029 | fact | 3M_2018_10K | capex | gold | – |
| 03031 | fact | LOCKHEEDMARTIN_2021_10K | working_capital | gold | – |
| 03069 | fact | AMD_2015_10K | da_margin | gold | – |
| 03282 | fact | NETFLIX_2017_10K | current_liabilities | gold | – |
| 03471 | fact | GENERALMILLS_2020_10K | current_ratio | gold | – |
| 03473 | fact | COCACOLA_2017_10K | roa | gold | – |
| 03531 | fact | NIKE_2019_10K | current_assets | gold | – |
| 03620 | fact | PEPSICO_2022_10K | ebitda, capex | gold | – |
| 03718 | fact | LOCKHEEDMARTIN_2022_10K | revenue_cagr | gold | – |
| 03838 | fact | BLOCK_2020_10K | revenue_growth | gold | – |
| 03849 | fact | MGMRESORTS_2020_10K | capex_pct_revenue | gold | – |
| 03856 | fact | ADOBE_2017_10K | ocf_ratio | gold | – |
| 03882 | fact | AMCOR_2020_10K | accounts_receivable | gold | – |
| 04080 | fact | NIKE_2021_10K | inventory_turnover | gold | – |
| 04103 | fact | GENERALMILLS_2019_10K | ccc | gold | – |
| 04171 | fact | MGMRESORTS_2018_10K | accounts_payable | gold | – |
| 04209 | fact | COSTCO_2021_10K | total_assets | gold | – |
| 04254 | fact | AMERICANWATERWORKS_2021_10K | ebitda | gold | – |
| 04302 | fact | NIKE_2018_10K | cogs_margin | gold | – |
| 04412 | fact | LOCKHEEDMARTIN_2020_10K | asset_turnover | gold | – |
| 04417 | fact | BESTBUY_2019_10K | inventory | gold | – |
| 04458 | fact | NETFLIX_2015_10K | ebitda_margin | gold | – |
| 04481 | fact | PEPSICO_2022_10K | ebitda_margin | gold | – |
| 04660 | fact | BLOCK_2016_10K | current_ratio | gold | – |
| 04672 | fact | 3M_2018_10K | ppe_net | gold | – |
| 04700 | fact | MICROSOFT_2016_10K | cogs | gold | – |
| 04735 | fact | ADOBE_2015_10K | ocf_ratio | gold | – |
| 04784 | fact | WALMART_2019_10K | operating_margin | gold | – |
| 04854 | fact | GENERALMILLS_2020_10K | fcf | gold | – |
| 04980 | fact | PEPSICO_2021_10K | capex | gold | – |
| 05718 | fact | AMERICANWATERWORKS_2020_10K | dividends_paid | gold | – |
| 05915 | fact | CVSHEALTH_2018_10K | fixed_asset_turnover | gold | – |
| 06247 | fact | WALMART_2018_10K | dpo | gold | – |
| 06272 | fact | COCACOLA_2022_10K | payout_ratio | gold | – |
| 06655 | fact | AMAZON_2017_10K | dpo | gold | – |
| 06741 | fact | WALMART_2020_10K | ebitda_margin | gold | – |
| 07507 | fact | ADOBE_2016_10K | operating_income_growth | gold | – |
| 07661 | fact | BLOCK_2020_10K | cfo | gold | – |
| 07966 | fact | ACTIVISIONBLIZZARD_2019_10K | capex_pct_revenue | gold | – |
| 08135 | fact | AMAZON_2017_10K | revenue_growth | gold | – |
| 08286 | fact | AMAZON_2019_10K | net_income | gold | – |
| 09724 | fact | COCACOLA_2021_10K | cogs_margin | gold | – |
| 10130 | fact | CORNING_2020_10K | dpo | gold | – |
| 10136 | fact | GENERALMILLS_2022_10K | retention_ratio | gold | – |
| 10285 | fact | BOEING_2018_10K | ppe_net | gold | – |
| 10420 | fact | AES_2022_10K | roa | gold | – |
| 10499 | fact | KRAFTHEINZ_2019_10K | inventory_turnover | gold | – |

## Values in the evidence text (`--verify`)

243 of 247 matched values appear in the question's FinanceBench evidence or justification, as statements print them ($M or $K). Unquoted values need a hand check:

| question | item | period | concept | value |
|---|---|---|---|---|
| 00552 | debt | FY2022 | LongTermDebt | 49,781,000,000 |
| 00552 | debt | FY2023 | LongTermDebt | 47,237,000,000 |
| 00956 | revenue | FY2021 | RevenueFromContractWithCustomerExcludingAssessedTax | 93,775,000,000 |
| 00956 | revenue | FY2022 | RevenueFromContractWithCustomerExcludingAssessedTax | 94,943,000,000 |
