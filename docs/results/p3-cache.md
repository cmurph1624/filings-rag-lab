# P3 cache: repeatable retrieval, and re-embedding only what changed

FinanceBench open subset (150 questions), distractor corpus. Migration
`012_caches.sql`, module `src/filings_rag/cache.py`.

## What's cached

| cache | key | used by |
|---|---|---|
| `embedding_cache` | embedder, dims, input type, sha256(text) | `frl ingest build` (chunks), `frl run` (queries) |
| `rerank_cache` | model, sha256(query), sha256(candidate texts in order), top_k | `frl run` |

Both are on by default. `--no-cache` on `frl run` and `frl ingest build` calls the APIs every
time.

## Why: retrieval wasn't repeatable

P3 PR 1 (`p3-noise.md`) found the top-k differed on 40/150 questions between runs of the same
config, because Voyage `rerank-3` returns scores at varying precision and near-ties reorder.
With the cache, retrieval of the P2 best config's retriever (retrieval-only, runs
1057, 1058, 1059) is identical on 150/150 questions:

| | run 1057 (fills the cache) | run 1058 | run 1059 |
|---|---|---|---|
| recall@10 | 0.796 | 0.796 | 0.796 |
| hit@10 | 0.852 | 0.852 | 0.852 |
| MRR | 0.636 | 0.636 | 0.636 |
| rerank billed | $0.1684 | $0.0000 | $0.0000 |
| rerank cost in the report (plain API) | $0.1684 | $0.1684 | $0.1684 |

## Spend stays honest

- A cache hit bills nothing. The call records `tokens = 0` and `cached_tokens` = what it cost
  first time. Embedding batches report one token total, so a batch's tokens are shared out
  across its texts by length.
- `frl report`'s *as recorded* view shows what was billed. Its *plain API* and *Batch API*
  views price cached reranks as if made fresh, so runs compare as deployed systems (the same
  rule as the scope model's cache). A query embedding counts its tokens even when cached (a
  few dozen per question).
- `frl ingest build` prints how many chunks came from the cache and what they would have
  cost, and `pipelines.embed_cached_tokens` keeps the total.

## Re-embedding only what changed

`tests/test_build_db.py::test_new_version_embeds_only_changed_chunks`: a new pipeline version
whose 10 chunks have one edited embeds only that one (5 of 50 tokens billed, 45 cached), and
an unchanged chunk's vector is byte-identical to the first version's.

## Caveat

The cache freezes whatever the first call returned. Runs before migration 012 (including
846, 960 and 980) aren't in the cache, so a new run of an old config won't reproduce their
retrieval exactly, only its own from then on.
