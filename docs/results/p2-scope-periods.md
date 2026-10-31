# P2 scope period options (retrieval-only)

**FinanceBench open subset, 150 questions. These are not full-set FinanceBench results.**

Three scope options target the P1 scope misses ([`p1-misses.md`](p1-misses.md)). Each is new
config: left out of the resolved spec at its off value, so P1 configs keep their hashes and their
behaviour. All runs: `structured@1` + rules scope + `rerank-3` over 50, corpus `fb-distractors@1`.
The baseline is run 615, the retrieval of P1's best config (run 638).

| option | run | recall@1 | recall@10 | hit@10 | MRR | gold filing in scope |
|---|---|---|---|---|---|---|
| none (run 615) | 615 | 0.450 | 0.776 | 0.832 | 0.624 | 147/150 |
| `forecast_lookback = 1` | 694 | 0.453 | 0.790 | 0.846 | 0.628 | 149/150 |
| `no_year = "latest"` | 695 | 0.440 | 0.756 | 0.812 | 0.607 | 139/150 |
| `fiscal_labels = "financebench"` | 696 | 0.456 | 0.783 | 0.839 | 0.631 | 147/150 |
| all three | 697 | 0.456 | 0.776 | 0.832 | 0.628 | 141/150 |
| **lookback + FinanceBench labels** | **698** | **0.463** | **0.796** | **0.852** | **0.641** | **149/150** |

What each option does and how it did:

- **`forecast_lookback = 1` helps.**
  - A forward-looking question ("expected", "forecast", "guidance", "outlook") also searches the
    annual filings a year before each year it names. A forecast for FY2023 is in the FY2022
    filing.
  - Six questions trigger it (00283, 00494, 00651, 01474, 01476, 02024).
  - The evidence for both P1 forecast misses is now retrieved: 00494 Boeing production rates at
    rank 1, and 00651 J&J EPS outlook at rank 4. Both were outside the top 10 before.
- **`fiscal_labels = "financebench"` helps the Ulta questions.**
  - It gives distractor filings `label_offset` from `companies.csv`. Ulta's is +1, because
    FinanceBench's FY2023 is the year Ulta calls fiscal 2022, as the gold answers say. Ulta's
    fiscal-2023 10-K is then labelled FY2024 and leaves the "FY2023" scope.
  - Evidence ranks for 00603, 00605 and 00606 rise from 5, 9 and 8 to 3, 5 and 4, and 00521 (Ulta
    acquisitions) is now found.
- **`no_year = "latest"` hurts, so it stays off.** Scoping questions that name no period to the
  company's latest year dropped the gold filing for all 8 of them (147 → 139 in scope). Their
  gold filings are older 10-Ks, 10-Qs and 8-Ks, not the latest year. Those 3 P1 misses (00601,
  02416, 00685) need another fix. 00601 names no company at all.

**Run 698 (lookback + FinanceBench labels) is the scope for the P2 ladder.** It gains hit@10 on
00494, 00521 and 00651 and loses none.

## `answer@3`

`answer@3` is `answer@1` plus two things:
- Each 10-K or 10-Q excerpt's header shows the period the filing covers ("fiscal year ended
  2023-01-28").
- A system note says the year in a filing's name uses the question's labels.

It targets the Ulta-style period confusion in generation. It is a generation change, so it is
measured in the PR 7 ladder, not here. The fact route's prompt will be `answer@4`.
