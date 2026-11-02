import contextlib
import csv
import tomllib
from collections import Counter
from pathlib import Path
from typing import Annotated

import psycopg
import typer

from filings_rag import cache, corpora, db
from filings_rag.backends.factory import make_backend
from filings_rag.benchmark import financebench
from filings_rag.config import get_settings
from filings_rag.edgar import expand, fetch, resolve
from filings_rag.edgar.client import EdgarClient
from filings_rag.embed.base import Embedder
from filings_rag.embed.voyage import VoyageEmbedder
from filings_rag.eval import gate, labels, noise, report, routes
from filings_rag.eval import judge as judging
from filings_rag.facts import concepts, coverage, lookup, metrics
from filings_rag.facts import load as facts_load
from filings_rag.harness import config as run_config
from filings_rag.harness import run as harness
from filings_rag.harness import setup
from filings_rag.harness.prompts import get_judge_prompt, get_prompt
from filings_rag.ingest import build
from filings_rag.ingest.registry import get_pipeline
from filings_rag.pricing import embedding_cost
from filings_rag.route import evaluate as route_eval
from filings_rag.route.router import make_router

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
CORPUS = ROOT / "corpus"

app = typer.Typer(no_args_is_help=True, help="Filings RAG Lab")
db_app = typer.Typer(no_args_is_help=True, help="Database commands")
app.add_typer(db_app, name="db")
bench_app = typer.Typer(no_args_is_help=True, help="Benchmark data")
app.add_typer(bench_app, name="bench")
corpus_app = typer.Typer(no_args_is_help=True, help="Filing corpus")
app.add_typer(corpus_app, name="corpus")
ingest_app = typer.Typer(no_args_is_help=True, help="Ingest pipelines")
app.add_typer(ingest_app, name="ingest")
facts_app = typer.Typer(no_args_is_help=True, help="XBRL facts")
app.add_typer(facts_app, name="facts")
route_app = typer.Typer(no_args_is_help=True, help="Query routing")
app.add_typer(route_app, name="route")
CONCEPTS = ROOT / "config" / "concepts.toml"


@db_app.command("migrate")
def db_migrate() -> None:
    """Apply pending SQL migrations."""
    applied = db.migrate(get_settings().database_url)
    typer.echo("\n".join(f"applied {n}" for n in applied) if applied else "up to date")


@bench_app.command("pull")
def bench_pull() -> None:
    """Download the pinned FinanceBench open subset and load it into Postgres."""
    src = financebench.download(DATA / "financebench")
    with psycopg.connect(get_settings().database_url) as conn:
        docs, questions = financebench.load(conn, src)
    typer.echo(f"loaded {questions} questions over {docs} documents")


@corpus_app.command("audit")
def corpus_audit() -> None:
    """Resolve every benchmark document to an EDGAR filing; write corpus/manifest.csv."""
    settings = get_settings()
    client = EdgarClient(settings.sec_user_agent, DATA / "edgar" / "cache")
    companies = resolve.read_companies(CORPUS / "companies.csv")
    overrides = resolve.read_overrides(CORPUS / "overrides.csv")
    with psycopg.connect(settings.database_url) as conn:
        cur = conn.execute("SELECT doc_name, company, doc_link FROM benchmark_docs")
        docs = [dict(zip(("doc_name", "company", "doc_link"), r, strict=True)) for r in cur]
    if not docs:
        typer.echo("no benchmark docs loaded; run `frl bench pull` first", err=True)
        raise typer.Exit(1)
    rows = [resolve.resolve(d, companies.get(d["company"]), client, overrides) for d in docs]
    resolve.write_manifest(rows, CORPUS / "manifest.csv")
    typer.echo(", ".join(f"{k}: {v}" for k, v in sorted(Counter(r.status for r in rows).items())))
    for r in rows:
        if r.status != "resolved":
            typer.echo(f"  {r.status:10} {r.doc_name}  {r.note}")


@corpus_app.command("expand")
def corpus_expand(
    ten_k_from: int = typer.Option(2015, help="First fiscal year of distractor 10-Ks"),
    ten_k_to: int = typer.Option(2023, help="Last fiscal year of distractor 10-Ks"),
    ten_q_from: int = typer.Option(2021, help="First fiscal year of distractor 10-Qs"),
    ten_q_to: int = typer.Option(2024, help="Last fiscal year of distractor 10-Qs"),
) -> None:
    """List the benchmark companies' other filings as distractors; write corpus/distractors.csv.

    Every company gets its other 10-Ks; companies with a 10-Q question also get other 10-Qs.
    """
    settings = get_settings()
    client = EdgarClient(settings.sec_user_agent, DATA / "edgar" / "cache")
    gold = fetch.read_manifest(CORPUS / "manifest.csv")
    companies = resolve.read_companies(CORPUS / "companies.csv")
    prefixes = expand.company_prefixes(gold)
    quarterly = expand.quarterly_companies(gold)
    window = expand.Window(range(ten_k_from, ten_k_to + 1), range(ten_q_from, ten_q_to + 1))
    gold_accessions = {r.accession for r in gold if r.accession}
    taken = {r.doc_name for r in gold}
    rows: list[resolve.ManifestRow] = []
    for company, cik in sorted(companies.items()):
        found, notes = expand.distractors(
            company,
            cik,
            prefixes[company],
            expand.wanted(window, company in quarterly),
            client.submissions(cik),
            resolve.periodic_filings(client.companyfacts(cik)),
            gold_accessions,
            taken,
        )
        rows += found
        for n in notes:
            typer.echo(f"  {n}")
    resolve.write_manifest(rows, CORPUS / "distractors.csv")
    typer.echo(
        f"{len(rows)} distractors: "
        + ", ".join(f"{k} {v}" for k, v in sorted(Counter(r.form for r in rows).items()))
    )


@corpus_app.command("fetch")
def corpus_fetch(
    only: str = typer.Option("", help="Fetch just this doc_name"),
    manifest: str = typer.Option("manifest.csv", help="Manifest under corpus/"),
) -> None:
    """Download every resolved manifest filing, extract text, and upsert `documents`."""
    settings = get_settings()
    client = EdgarClient(settings.sec_user_agent, DATA / "edgar" / "cache")
    with (CORPUS / "companies.csv").open() as fh:
        tickers = {r["company"]: r["ticker"] for r in csv.DictReader(fh)}
    rows = [r for r in fetch.read_manifest(CORPUS / manifest) if not only or r.doc_name == only]
    failed = 0
    with psycopg.connect(settings.database_url) as conn:
        for r in rows:
            try:
                n = fetch.fetch_document(conn, client, r, tickers.get(r.company), DATA)
                typer.echo(f"  ok   {r.doc_name:40} {n:>9,} chars")
            except Exception as e:  # report and keep going; one bad filing shouldn't stop the batch
                conn.rollback()
                failed += 1
                typer.echo(f"  FAIL {r.doc_name:40} {e}", err=True)
    typer.echo(f"fetched {len(rows) - failed}/{len(rows)}")
    if failed:
        raise typer.Exit(1)


@corpus_app.command("define")
def corpus_define(
    ref: str = typer.Argument(..., help="name@version, e.g. fb-distractors@1"),
) -> None:
    """Record a corpus's filings from its manifests (immutable once defined)."""
    _, _, corpus = corpora.parse_ref(ref)
    accessions = {
        r.accession
        for m in corpus.manifests
        for r in fetch.read_manifest(CORPUS / m)
        if r.source == "edgar" and r.accession
    }
    with psycopg.connect(get_settings().database_url) as conn:
        n = corpora.define(conn, ref, accessions)
    typer.echo(f"{ref}: {n} filings")


@ingest_app.command("build")
def ingest_build(
    pipeline: str = typer.Argument(..., help="name@version, e.g. naive-fixed@1"),
    only: str = typer.Option("", help="Build just this doc_name"),
    no_cache: bool = typer.Option(False, help="Embed every chunk, ignoring the embedding cache"),
) -> None:
    """Chunk, embed and index every document for one pipeline version (resumable).

    Chunks whose text was embedded before (by any pipeline, same embedder) reuse that vector."""
    settings = get_settings()
    p = get_pipeline(pipeline)
    embedder: Embedder = VoyageEmbedder(settings.voyage_api_key)
    with contextlib.ExitStack() as stack:
        if not no_cache:
            embedder = cache.CachedEmbedder(
                embedder, stack.enter_context(cache.connect(settings.database_url))
            )
        conn = stack.enter_context(psycopg.connect(settings.database_url))
        stats = build.build(conn, p, embedder, docs=[only] if only else None, log=typer.echo)
    cost = embedding_cost(embedder.name, stats.embed_tokens)
    saved = embedding_cost(embedder.name, stats.cached_tokens)
    typer.echo(
        f"{pipeline}: {stats.documents} documents ({stats.empty} empty), {stats.chunks:,} chunks, "
        f"{stats.embed_tokens:,} embedding tokens (${cost:.2f} at list); "
        f"{stats.cached_chunks:,} chunks from cache ({stats.cached_tokens:,} tokens, "
        f"${saved:.2f} not re-billed)"
    )


@ingest_app.command("label")
def ingest_label(pipeline: str = typer.Argument(..., help="name@version")) -> None:
    """Map FinanceBench evidence passages to this pipeline's chunks."""
    p = get_pipeline(pipeline)
    with psycopg.connect(get_settings().database_url) as conn:
        r = labels.label(conn, p)
    typer.echo(
        f"{pipeline}: {r.labelled}/{r.items} evidence passages labelled "
        f"({r.labels} chunk labels); page agreement {r.page_agree}/{r.page_checked}"
    )
    for m in r.missing:
        typer.echo(f"  unlabelled {m}")
    for b in r.broad:
        typer.echo(f"  too generic (short passage, many chunks) {b}")


@facts_app.command("load")
def facts_load_cmd() -> None:
    """Load SEC companyfacts (XBRL) for every company in corpus/companies.csv into xbrl_facts."""
    settings = get_settings()
    client = EdgarClient(settings.sec_user_agent, DATA / "edgar" / "cache")
    with CORPUS.joinpath("companies.csv").open() as fh:
        ciks = [int(r["cik"]) for r in csv.DictReader(fh)]
    with psycopg.connect(settings.database_url) as conn:
        for cik in ciks:
            s = facts_load.load_company(conn, cik, client.companyfacts(cik))
            typer.echo(f"CIK {cik}: {s.rows:,} facts ({s.duplicates} repeats skipped)")


@facts_app.command("get")
def facts_get_cmd(
    doc_name: str = typer.Argument(..., help="Anchor filing, e.g. AMAZON_2017_10K"),
    metric: str = typer.Argument(..., help="A metric or item from config/concepts.toml"),
    period: str = typer.Argument(..., help='FinanceBench period, e.g. FY2017 or "Q2 FY2023"'),
    option: Annotated[
        list[str] | None, typer.Option(help="Metric option as key=value (repeatable)")
    ] = None,
) -> None:
    """Compute one metric from XBRL facts, as filed in the anchor filing where possible."""
    opts: dict[str, object] = {}
    for kv in option or []:
        k, _, raw = kv.partition("=")
        opts[k] = raw  # metrics.compute coerces strings to the declared choices
    cmap = concepts.load(CONCEPTS)
    with psycopg.connect(get_settings().database_url) as conn:
        try:
            facts = lookup.FactLookup(
                conn, cmap, doc_name, lookup.read_offsets(CORPUS / "companies.csv")
            )
            v = metrics.compute(facts, metric, lookup.parse_period(period), **opts)
        except (metrics.MissingInput, ValueError, ZeroDivisionError) as e:
            typer.echo(f"cannot compute: {e}", err=True)
            raise typer.Exit(1) from e
    typer.echo(f"{metric} {period} ({metrics.VERSION}): {v.display()}  [{v.value:.6g}]")
    typer.echo(f"  = {v.formula}")
    for f in v.facts:
        src = "as filed" if f.as_filed else f"from {f.accession}"
        typer.echo(
            f"  {f.item} {lookup.period_label(f.period)}: {f.value:,.0f} ({f.concept}, {src})"
        )


@route_app.command("eval")
def route_eval_cmd(
    mode: str = typer.Option("rules", help="rules | llm"),
    model: str = typer.Option("", help="llm mode: the model (default claude-haiku-5-5)"),
) -> None:
    """Score a router against the hand route labels (routes@1), without generation."""
    spec: dict[str, object] = {"mode": mode}
    if model:
        spec["model"] = model
    labels = routes.load(CORPUS / "route_labels.csv")
    with psycopg.connect(get_settings().database_url) as conn:
        router = make_router(conn, spec)
        assert router is not None
        questions: dict[str, str] = dict(
            conn.execute("SELECT id, question FROM questions").fetchall()
        )
        scores, cost = [], 0.0
        for qid in sorted(labels):
            plan, aux = router.route(qid, questions[qid])
            cost += float(aux["cost_usd"]) if aux else 0.0
            scores.append(route_eval.RouteScore(qid, labels[qid], plan))
    typer.echo(f"router {router.spec}; model spend ${cost:.4f} (API-equivalent)")
    typer.echo(route_eval.summarize(scores))


@route_app.command("facts")
def route_facts_cmd(run_id: int = typer.Argument(..., help="A run made with a [router]")) -> None:
    """Judge-free fact-route accuracy: for fact-labelled questions whose gold answer is a bare
    number, does the headline computed fact equal it?"""
    labels = routes.load(CORPUS / "route_labels.csv")
    with psycopg.connect(get_settings().database_url) as conn:
        checks = route_eval.fact_route_accuracy(conn, run_id, labels)
    ok = sum(c.ok for c in checks)
    typer.echo(f"run {run_id}: {ok}/{len(checks)} headline facts equal the gold number")
    for c in checks:
        if not c.ok:
            typer.echo(f"  {c.question_id[16:]}  gold {c.gold}  fact {c.shown or '–'}")


@app.command("gate")
def gate_cmd(
    pipeline: str = typer.Option(
        "",
        help="Check this pipeline (name@version) instead of each config's own: it is built"
        " over the gate corpus (re-embedding only changed chunks) and labelled first",
    ),
    gate_config: str = typer.Option("config/gate.toml", "--config", help="Gate config"),
    out: str = typer.Option("", help="Also write the markdown verdict to this file"),
    no_cache: bool = typer.Option(False, help="Ignore the embedding and rerank caches"),
) -> None:
    """Retrieval gate: run each gated config retrieval-only and compare it with its pinned
    baseline. Exits 1 when a metric drops by more than `config/gate.toml` allows."""
    cfg_gate = gate.load_config(ROOT / gate_config, ROOT)
    settings = get_settings()
    git = harness.git_info(ROOT)
    route_labels = routes.load(CORPUS / "route_labels.csv")
    with contextlib.ExitStack() as stack:
        conn = stack.enter_context(psycopg.connect(settings.database_url))
        cache_conn = None if no_cache else stack.enter_context(cache.connect(settings.database_url))
        _, _, corpus = corpora.parse_ref(cfg_gate.corpus)
        accessions = {
            r.accession
            for m in corpus.manifests
            for r in fetch.read_manifest(CORPUS / m)
            if r.source == "edgar" and r.accession
        }
        corpora.define(conn, cfg_gate.corpus, accessions)  # no-op once defined; checks it
        if pipeline:
            p = get_pipeline(pipeline)
            embedder: Embedder = VoyageEmbedder(settings.voyage_api_key)
            if cache_conn is not None:
                embedder = cache.CachedEmbedder(embedder, cache_conn)
            docs = corpora.doc_names(conn, cfg_gate.corpus)
            typer.echo(f"building {pipeline} over {cfg_gate.corpus} ({len(docs)} filings)")
            stats = build.build(conn, p, embedder, docs=docs, log=lambda _: None)
            typer.echo(
                f"  {stats.documents} new filings, {stats.chunks:,} chunks:"
                f" {stats.embed_tokens:,} tokens embedded, {stats.cached_chunks:,} chunks cached"
            )
            lr = labels.label(conn, p)
            typer.echo(f"  labelled {lr.labelled}/{lr.items} evidence passages")
        results = []
        for check in cfg_gate.checks:
            raw = tomllib.loads(check.config.read_text())
            if pipeline:
                raw["pipeline"] = pipeline
            if raw.get("corpus") != cfg_gate.corpus:
                typer.echo(f"{check.config.name}: corpus must be {cfg_gate.corpus}", err=True)
                raise typer.Exit(1)
            cfg = run_config.from_dict(raw)
            if cfg.backend_spec is not None:
                typer.echo(f"{check.config.name}: gate configs are retrieval-only", err=True)
                raise typer.Exit(1)
            try:
                side = setup.retrieval_side(
                    conn, cfg, settings, CORPUS / "companies.csv", cache_conn
                )
                baseline = gate.measure(conn, check.baseline, route_labels)
            except ValueError as e:
                typer.echo(f"{check.config.name}: {e}", err=True)
                raise typer.Exit(1) from e
            run_id = harness.create_run(conn, cfg, git)
            typer.echo(f"{cfg.name}: run {run_id} vs baseline {check.baseline}")
            s = harness.execute(
                conn, run_id, cfg, side.retriever, None, None, git,
                scoper=side.scoper, router=side.router, log=lambda _: None,
            )  # fmt: skip
            if s.failed or s.stopped_early:
                typer.echo(f"{cfg.name}: run {run_id} had {s.failed} failures", err=True)
                raise typer.Exit(1)
            candidate = gate.measure(conn, run_id, route_labels)
            results.append(gate.compare(cfg.name, baseline, candidate, cfg_gate.allowed_drop))
    text = gate.render(results, cfg_gate.corpus)
    if out:
        Path(out).write_text(text)
    typer.echo(text)
    if not all(r.ok for r in results):
        raise typer.Exit(1)


@app.command("noise")
def noise_cmd(
    run_ids: Annotated[list[int], typer.Argument(help="Runs of the same config (two or more)")],
    replicates: int = typer.Option(
        1, help="Also compare the first run's judge replicates 1..N (`frl judge --replicate`)"
    ),
    out: str = typer.Option("", help="Also write the markdown to this file"),
) -> None:
    """How much results move when nothing changes: generation, judge, retrieval, facts."""
    if len(run_ids) < 2 and replicates < 2:
        typer.echo("give two or more runs, or --replicates 2+", err=True)
        raise typer.Exit(1)
    with psycopg.connect(get_settings().database_url) as conn:
        try:
            runs = [noise.load(conn, r) for r in run_ids]
            reps = [noise.load(conn, run_ids[0], i) for i in range(1, replicates + 1)]
        except ValueError as e:
            typer.echo(f"cannot compare: {e}", err=True)
            raise typer.Exit(1) from e
    problems = noise.check_comparable(runs) + noise.check_comparable(reps, replicates=True)
    if problems:
        typer.echo("cannot compare:\n  " + "\n  ".join(problems), err=True)
        raise typer.Exit(1)
    gen = noise.agreement([(f"run {r.run_id}", r.verdicts) for r in runs])
    judge = (
        noise.agreement([(r.judge.split(":")[-1], r.verdicts) for r in reps])
        if replicates > 1
        else None
    )
    text = noise.render(
        gen,
        judge,
        noise.identical_share([r.retrieved for r in runs]),
        noise.identical_share([{q: sorted(v) for q, v in r.retrieved.items()} for r in runs]),
        noise.identical_share([r.facts for r in runs], skip_empty=True),
    )
    if out:
        Path(out).write_text(text)
    typer.echo(text)


@facts_app.command("coverage")
def facts_coverage_cmd(
    out: str = typer.Option("", help="Also write the markdown to this file"),
    verify: bool = typer.Option(
        False, help="Also look for each found value in the question's evidence text"
    ),
) -> None:
    """Are fact and mixed questions' inputs in xbrl_facts, as filed in their gold filing?"""
    cmap = concepts.load(CONCEPTS)
    route_labels = routes.load(CORPUS / "route_labels.csv")
    with psycopg.connect(get_settings().database_url) as conn:
        results = coverage.check(conn, route_labels, cmap, CORPUS / "companies.csv")
        text = coverage.render(results, cmap.version)
        if verify:
            text += coverage.render_verify(coverage.verify(conn, results))
    if out:
        Path(out).write_text(text)
    typer.echo(text)


@app.command("run")
def run_cmd(
    config: str = typer.Argument(..., help="Run config, e.g. configs/p0-naive.toml"),
    limit: int | None = typer.Option(None, help="Only the first N questions (by id)"),
    resume: int | None = typer.Option(None, help="Continue this run id"),
    only: str = typer.Option(
        "",
        help="Only the question ids listed in this file, one per line. The run is partial: its"
        " report shows answered N/150, and a later --resume without --only fills in the rest.",
    ),
    no_cache: bool = typer.Option(
        False, help="Call the embedder and reranker every time, ignoring their caches"
    ),
) -> None:
    """Answer the question set with one config and record everything in `runs`.

    Query embeddings and reranks come from their caches when the same call was made before,
    which makes retrieval repeatable; a cache hit bills nothing and is recorded as cached."""
    cfg = run_config.load(Path(config))
    settings = get_settings()
    git = harness.git_info(ROOT)
    if git.dirty:
        typer.echo("warning: uncommitted changes; the recorded git SHA won't reproduce this run")
    with contextlib.ExitStack() as stack:
        conn = stack.enter_context(psycopg.connect(settings.database_url))
        cache_conn = None if no_cache else stack.enter_context(cache.connect(settings.database_url))
        try:
            side = setup.retrieval_side(conn, cfg, settings, CORPUS / "companies.csv", cache_conn)
        except ValueError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1) from e
        retriever, scoper, router = side.retriever, side.scoper, side.router
        spec = cfg.backend_spec
        backend = make_backend(spec) if spec else None  # None: retrieval-only run
        prompt = get_prompt(cfg.generation["prompt"]) if cfg.generation else None
        subset = None
        if only:
            subset = {ln.strip() for ln in Path(only).read_text().splitlines() if ln.strip()}
            known = {
                r[0]
                for r in conn.execute(
                    "SELECT id FROM questions WHERE benchmark = %s", (cfg.questions,)
                )
            }
            if unknown_ids := sorted(subset - known):
                typer.echo(f"--only lists ids not in {cfg.questions}: {unknown_ids[:5]}", err=True)
                raise typer.Exit(1)
            typer.echo(f"partial run: only the {len(subset)} questions in {only}")
        if resume is not None:
            harness.check_resume(conn, resume, cfg)
            run_id = resume
        else:
            run_id = harness.create_run(conn, cfg, git)
        typer.echo(f"run {run_id}: {cfg.name} on {cfg.corpus} (config {cfg.hash})")
        s = harness.execute(
            conn,
            run_id,
            cfg,
            retriever,
            backend,
            prompt,
            git,
            scoper=scoper,
            router=router,
            limit=limit,
            only=subset,
            log=typer.echo,
        )
    u = s.usage
    typer.echo(
        f"run {run_id}: {s.answered} {'answered' if backend else 'retrieved'}, {s.failed} failed,"
        f" {s.skipped} already done"
        f"{' (stopped early)' if s.stopped_early else ''}{' — complete' if s.finished else ''}\n"
        f"  tokens: {u.input_tokens:,} in, {u.output_tokens:,} out, {u.cache_read_tokens:,} cache"
        f" read, {u.cache_write_tokens + u.cache_write_1h_tokens:,} cache write;"
        f" {s.embed_tokens:,} query-embedding\n"
        f"  API-equivalent cost: ${s.gen_cost + s.embed_cost + s.aux_cost:.4f}"
        f" (generation ${s.gen_cost:.4f}, embedding ${s.embed_cost:.6f},"
        f" scope/rerank models ${s.aux_cost:.4f});"
        f" backend reported ${s.reported_cost:.4f}"
        + (
            f"\n  {s.cached_reranks} questions reranked from the cache (not billed here;"
            " `frl report` prices them in its plain/Batch API views)"
            if s.cached_reranks
            else ""
        )
    )
    if s.paused:  # a clean stop, not a failure: exit 3 like `usage gate`
        raise typer.Exit(3)
    if s.failed or s.stopped_early:
        raise typer.Exit(1)


@app.command("judge")
def judge_cmd(
    run_id: int = typer.Argument(..., help="Run to grade"),
    only: str = typer.Option(
        "", help="Only these question ids (a file, one per line): disjoint shards run in parallel"
    ),
    replicate: int = typer.Option(
        1,
        help="Judge again as replicate N (>= 2): verdicts are stored under the judge key with"
        " '#rN', leaving the run's own verdicts untouched. For measuring judge agreement.",
    ),
) -> None:
    """Grade a run's answers with the judge pinned in its config (resumable)."""
    with psycopg.connect(get_settings().database_url) as conn:
        row = conn.execute("SELECT config FROM runs WHERE id = %s", (run_id,)).fetchone()
        if row is None:
            typer.echo(f"no run {run_id}", err=True)
            raise typer.Exit(1)
        try:
            cfg = run_config.from_dict(row[0])
        except ValueError as e:  # e.g. runs stored before judge prompts were versioned
            typer.echo(f"run {run_id}'s stored config no longer loads ({e}); re-run it", err=True)
            raise typer.Exit(1) from e
        if cfg.judge is None or cfg.judge_spec is None or cfg.judge_key is None:
            typer.echo(f"run {run_id} has no [judge] (retrieval-only run)", err=True)
            raise typer.Exit(1)
        shard = None
        if only:
            path = Path(only)
            if not path.is_file():
                typer.echo(f"--only: no file {only}", err=True)
                raise typer.Exit(1)
            shard = {ln.strip() for ln in path.read_text().splitlines() if ln.strip()}
            answered = {
                r[0]
                for r in conn.execute(
                    "SELECT question_id FROM run_questions"
                    " WHERE run_id = %s AND answer IS NOT NULL",
                    (run_id,),
                )
            }
            if not shard or not (shard & answered):
                typer.echo(f"--only: no answered question of run {run_id} in {only}", err=True)
                raise typer.Exit(1)
            if missing := sorted(shard - answered):
                typer.echo(f"--only: {len(missing)} ids have no answer in run {run_id}", err=True)
        if replicate < 1:
            typer.echo("--replicate must be 1 or more", err=True)
            raise typer.Exit(1)
        judge_key = cfg.judge_key if replicate == 1 else f"{cfg.judge_key}#r{replicate}"
        typer.echo(f"run {run_id}: judging with {judge_key}")
        s = judging.judge_run(
            conn,
            run_id,
            judge_key,
            make_backend(cfg.judge_spec),
            get_judge_prompt(cfg.judge["prompt"]),
            concurrency=cfg.execution["concurrency"],
            only=shard,
            log=typer.echo,
        )
    typer.echo(
        f"run {run_id}: {s.judged} judged, {s.failed} failed, {s.skipped} already judged;"
        f" judge cost ${s.cost:.4f} (API-equivalent)"
    )
    if s.paused:
        raise typer.Exit(3)
    if s.failed:
        raise typer.Exit(1)


@app.command("report")
def report_cmd(
    run_id: int = typer.Argument(..., help="Run to report on"),
    vs: Annotated[list[int] | None, typer.Option(help="Other runs alongside (repeatable)")] = None,
    out: str = typer.Option("", help="Also write the markdown to this file"),
    retrieval_only: bool = typer.Option(False, help="Only identity, retrieval and latency"),
) -> None:
    """Accuracy, retrieval recall, cost and latency for one or more runs, as markdown."""
    with psycopg.connect(get_settings().database_url) as conn:
        try:
            summaries = [report.summarize(conn, r) for r in [run_id, *(vs or [])]]
            labels_path = CORPUS / "route_labels.csv"
            route_labels = routes.load(labels_path) if labels_path.exists() else None
            text = report.render(summaries, retrieval_only=retrieval_only, routes=route_labels)
        except ValueError as e:  # unknown run, or a stored config that no longer loads
            typer.echo(f"cannot report: {e}", err=True)
            raise typer.Exit(1) from e
    if out:
        Path(out).write_text(text)
    typer.echo(text)


if __name__ == "__main__":
    app()
