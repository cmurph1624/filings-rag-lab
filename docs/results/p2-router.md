# P2 router and fact route

**FinanceBench open subset, 150 questions. These are not full-set FinanceBench results.**

A `[router]` in a run config routes each question to **fact**, **mixed** or **prose** (as in
`routes@1`). Fact and mixed plans get a **Facts** section in the prompt (`answer@4`): values
computed in code from XBRL (`metrics@1`), each with its formula and source line items, ahead of
the retrieved excerpts. Prose questions are answered exactly as before.

## How it works

- **Plan:** route, metrics (concept-map names), the periods the answer needs data from, options
  for formulas the question spells out (DPO over purchases), and an aggregate (a multi-year
  average). It is stored on the run row (`run_questions.route`) with the anchor filing and every
  computed fact.
- **Routers:**
  - `rules` (`route/plan.py`): metric phrases, prose cues (drivers, segments, guidance,
    non-GAAP…) and judgment cues ("is it healthy", "if not useful…").
  - `llm`: Haiku 5.5 with prompt `route@1`, returning JSON, cached in `query_routes`. Unknown
    metric names and bad periods in a reply are dropped.
- **Anchor filing:** from the question's **scope**, never the gold label. It is the scope's 10-K
  or 10-Q for the plan's latest period, else its latest 10-K or 10-Q. With no company found
  (scope fell back to the whole corpus), there are no facts.
- **Targets:**
  - CAGR runs first → last period.
  - Growth and average-balance metrics use the earliest period as the prior year.
  - Everything else is computed per period. An average is added only when every period
    computed.
  - Failures are listed as unavailable, with the reason.

## Router accuracy against `routes@1` (`frl route eval`)

| router | route | metric set exact | periods exact | model spend |
|---|---|---|---|---|
| rules | **146/150** | 77/79 | 134/150 | – |
| llm (Haiku, `route@1`) | 142/150 | 67/80 | 125/150 | $0.06 |

**Caveat:** the rules router's regexes were refined against these labels during this PR. The
labels were frozen first (#19), but 146/150 still flatters `rules`. The `llm` prompt was written
once and not tuned, so it is the fairer measure of how hard routing is.

The `llm` router's misses:

- **Extra metrics:** it often lists a metric's inputs too (dpo, accounts_payable, cogs,
  inventory). That is harmless for answering.
- **Invented periods:** for "historically" questions it invents future years (FY2023–FY2026).
- **Prose sent to facts:** it sends 5 prose questions (bank margins, the JPM liquidation, the
  largest liability) to fact or mixed.

The ladder uses `rules`, the better router on these numbers (P2 decision 3).

## Fact route without generation (`frl route facts`)

For the 50 fact-labelled questions whose gold answer is a bare number, the check compares the
headline computed fact with the gold number. The headline is the average if there is one, else
the last value. Runs are retrieval-only (no model calls).

| router | run | headline fact = gold |
|---|---|---|
| rules | 844 | **46/50** |
| llm | 845 | 42/50 |

The four `rules` misses are not wrong facts:

- **Two-step answers:** 03620 (EBITDA less capex) and 04784 (change in operating margin) need
  the generator to subtract two facts, and both facts are given.
- **Gold rounding:** 04980 and 05718 are FinanceBench rounding. PepsiCo's FY2021 capex is
  $4,625M against the gold's "$4.60" billion, and American Water's FY2020 dividends are $389M
  against "$0.40" billion.

The `llm` router's four extra misses are a scoring artifact. It lists inputs after the metric,
so the "headline" becomes an input (inventory, total assets) rather than DPO or ROA.

## Smoke run: the 63 fact questions with `answer@4`

Run 846 (`configs/p2-04-router-rules-facts.toml`, `--only` the 63 `routes@1` fact questions),
judged by Opus `judge@1`, against run 638 (P1's best, `answer@1`, no router) on the same
questions:

| | run 638 | run 846 |
|---|---|---|
| correct | 58/63 (92.1%) | **62/63 (98.4%)** |
| incorrect | 1 | 1 |
| refused | 4 | 0 |
| prompt tokens per question | 8,217 | 8,756 (+7%) |

- **Fixed 4, broke 0.** The four are exactly the P1 retrieval misses where the needed statement
  wasn't in the top 10 ([`p1-misses.md`](p1-misses.md)): 00302 Pfizer PP&E, 04481 PepsiCo
  EBITDA margin, 06655 Amazon DPO and 10130 Corning DPO. Each was a refusal and is now correct
  from the Facts section.
- **The one miss is 00585**, Boeing's effective tax rate: the sign convention for a tax benefit
  on a pretax loss, already filed as debatable gold.
- **The answers use the facts.** 61 of 63 questions got a Facts section, and 60 answers cite
  `[F…]`. 57 of the 60 with a headline fact print its number.

This is a smoke run on the questions the fact route is built for, with labels that pick them.
The headline comparison over all 150 questions, with the prose questions where facts don't
apply, is the PR 7 ladder.
