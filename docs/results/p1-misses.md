# P1 failure buckets: the 29 misses of run 638

**FinanceBench open subset, 150 questions. These are not full-set FinanceBench results.**
Run 638 is the best P1 config: `structured@1` + rules scope + `rerank-3` over 50, `answer@1`,
judged by Opus 5.5 `judge@1`. It got 121 right, 19 wrong and refused 10
([`p1-retrieval.md`](p1-retrieval.md)). Each miss below was read by hand and assigned one primary
cause, the same way as P0's buckets in [`p0-baseline.md`](p0-baseline.md#failure-buckets-all-50-misses-each-assigned-its-primary-cause-by-hand).

"Evidence ranks" are from the run's stored retrieval, scored against FinanceBench's evidence
passages: the best rank of each labelled passage in the top 10, or "–" when it isn't there.

## Summary

| bucket | n | P0 (run 60) | stage |
|---|---|---|---|
| Evidence not retrieved | 6 | 19 | retrieval |
| Multi-input calculation missing an input | 4 | 7 | retrieval |
| Passage only partly retrieved | 1 | 3 (table split) | retrieval |
| Fiscal-year label convention (Ulta) | 3 | – | scope / prompt |
| Forward-looking question scoped to the wrong year | 2 | 4 (wrong period/company) | scope |
| Question names no year or company | 3 | | scope |
| Definition or judgment differs from gold | 5 | 8 | generation |
| Hedged between answers | 1 | 4 | generation |
| Gold or judge debatable | 4 | 3 | eval |
| Over-cautious with evidence present | 0 | 2 | generation |
| **total** | **29** | **50** | |

By stage:

- **Retrieval: 11 misses**, down from 29 in P0. Four of the 6 straight misses are a single
  financial statement (balance sheet or income statement) that wasn't in the top 10.
- **Scoping: 8.** None of these existed as a separate cause in P0, because P0 had no scope step.
  Most are questions whose year doesn't map one-to-one to a filing.
- **Generation and eval: 10.** About the same as P0's 17, minus the hedges.

P0's buckets were assigned on a different corpus (the 84 gold filings only), so the P0 column is
context, not a like-for-like comparison.

## The misses

### Retrieval (11)

| question | verdict | what happened | evidence ranks |
|---|---|---|---|
| 00669 JnJ FY22 gross margin drivers | incorrect | MD&A passage with the drivers not retrieved; answered from the income statement alone | – |
| 01911 MGM FY22 interest coverage (adj. EBIT) | incorrect | Q4 earnings release (gold doc) in scope but not retrieved; used 10-K operating income | – |
| 00080 PayPal FY22 working capital | refused | Balance sheet not retrieved | – |
| 00302 Pfizer PP&E FY20 vs FY21 | refused | Balance sheet not retrieved | – |
| 00678 Boeing FY22 gross margin | refused | Income statement (cost of sales) not retrieved | – |
| 00799 Amcor quick ratio FY22 vs FY23 | refused | Balance sheet not retrieved | – |
| 00540 AES FY22 inventory turnover | refused | Had inventory, missing cost of sales | 1, – |
| 04481 PepsiCo FY22 EBITDA margin | refused | Cash-flow statement header retrieved without its D&A line | –, – |
| 06655 Amazon FY17 DPO | refused | Had payables and inventory, missing COGS | –, 1 |
| 10130 Corning FY20 DPO | refused | Had payables and inventory, missing COGS | –, 2 |
| 01079 Amcor acquisitions FY21–23 | incorrect | Acquisitions note retrieved, but only two of the three FY23 deals were in the chunk | 1 |

### Scoping (8)

| question | verdict | what happened |
|---|---|---|
| 00603 Ulta FY23 inventory drivers | incorrect | FinanceBench's "FY2023" is Ulta's fiscal 2022 (ended Jan 28, 2023). Scope correctly held both filings; the model answered for fiscal 2023 (ended Feb 3, 2024). |
| 00605 Ulta FY23 Q4 buyback share | incorrect | Same convention |
| 00606 Ulta FY23 wages % of sales | incorrect | Same convention |
| 00494 Boeing FY23 production-rate forecast | incorrect | The forecast is in the FY2022 10-K. Rules scoped "FY2023" to the FY2023 10-K, which reports actual rates. |
| 00651 JnJ FY23 adjusted EPS outlook | refused | The guidance is in the FY2022 Q4 earnings release. Rules scoped to FY2023 documents. |
| 00601 SG&A % of sales drivers, FY23 | incorrect | The question names no company (FinanceBench assumes the document is known). Scope fell back to the whole corpus; the model picked Amcor. |
| 02416 Pfizer acquisitions "in this 10K" | incorrect | No year in the question. Scope kept every Pfizer filing; the excerpts came from five other years. |
| 00685 Best Buy gross margins "historically" | refused | No year. Scope kept every Best Buy filing; reranking favoured FY2015–2019 risk factors over the FY2023 income statement. |

### Generation and eval (10)

| question | verdict | bucket | what happened |
|---|---|---|---|
| 00005 Corning FY22 working capital | incorrect | definition | Standard WC ($2,278M) vs gold's operating-only WC ($831M) |
| 00807 3M Q2 FY23 quick ratio | incorrect | definition | (cash + securities + AR) / CL = 0.85 vs gold's (CA − inventory) / CL = 0.96 |
| 02119 JPM liquidation value per share | incorrect | definition | Book value ($82) as the headline; gold uses tangible book ($66.56) |
| 00723 AMEX operating margin | incorrect | judgment | Called the metric "imperfect" and computed it anyway; gold says it isn't used for a lender |
| 00790 CVS capital intensity | incorrect | judgment | Same facts (5.6% fixed assets), opposite conclusion |
| 01902 Best Buy best category Q2 FY24 | incorrect | hedge | Led with the biggest category by revenue; named the gold (fastest-growing) as the alternative |
| 00283 Pfizer remaining Upjohn costs | incorrect | gold debatable | $70M (10% of $700M) vs gold $77.78M (700/9) |
| 00585 Boeing effective tax rate | incorrect | gold debatable | Same inputs; opposite sign convention for a tax benefit on a loss |
| 01328 PepsiCo restructuring on the income statement | incorrect | gold debatable | Answered 0 because the $411M sits inside other lines, not as its own line (the question's own rule) |
| 02419 Pfizer spin-off as of Q2 2023 | incorrect | gold debatable | Gold reads the completed 2020 Upjohn spin-off as ongoing |

## Cross-checks with other runs

**The other pipelines often get these right.** Of the 11 retrieval misses, run 636 (`naive-fixed@1`,
same scope and rerank) answered 4 correctly: 00669, 00678, 04481 and 06655. In each, naive's
chunks put the needed statement in the top 10, but `structured@1` didn't. Run 637
(`table-preserving@1`) got 00302 and 10130. So a statement table that one pipeline ranks highly can
be missing from another's top 10. This is the cost of `structured@1`'s lower recall@10 (0.776 vs
0.815) that accuracy averaged over.

**Perfect filing selection fixes most scope misses.** Run 639 (oracle scope + `rerank-3`,
retrieval-only, `naive-fixed@1`, an upper bound) has recall@10 0.903 against 0.815 for rules +
rerank (run 390). Run 390 finds no labelled evidence for 00494, 00651, 00685, 01079 or 02416. Run
639 finds it for all of them except 00685, and also for 01911.

## What this says for P2

1. **XBRL facts would cover 8 of the 11 retrieval misses.** Four are a missing balance-sheet or
   income-statement line and four are ratio inputs (DPO, inventory turnover, EBITDA). These are
   P2's "fact questions to SQL" case, and they're exactly the questions chunk retrieval keeps
   dropping.
2. **Scope needs period semantics, not just a year match:**
   - A forward-looking question ("expected in FY2023") should also search the prior year's filing
     (2 misses).
   - Ulta's FinanceBench year label is one ahead of the company's fiscal year. The generator needs
     to be told which period the label means (3 misses). Scope already handles it as data, but
     the prompt doesn't.
   - Questions with no year are better routed to the gold-document context FinanceBench assumes,
     or answered with the latest filing (3 misses).
3. **Ten misses aren't retrieval at all.** Five are metric definitions. A shared definition table
   (working capital, quick ratio, DPO) given to the generator, or computed in SQL, would remove
   that ambiguity. Four are judged against debatable gold answers. They're the floor for this
   question set, and shouldn't be chased.

Method: one primary cause per miss, read from the question, gold answer and justification, the
model's answer, the judge's rationale, the run's scope result and retrieved documents, and the
evidence ranks. Assigned by Claude and not yet reviewed by Chris.
