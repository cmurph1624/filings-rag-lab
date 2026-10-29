import csv
from collections import Counter
from pathlib import Path
from typing import Annotated

import psycopg
import typer

from filings_rag import db
from filings_rag.backends.factory import make_backend
from filings_rag.benchmark import financebench
from filings_rag.config import get_settings
from filings_rag.edgar import fetch, resolve
from filings_rag.edgar.client import EdgarClient
from filings_rag.embed.factory import make_embedder
from filings_rag.embed.voyage import VoyageEmbedder
from filings_rag.eval import judge as judging
from filings_rag.eval import labels, report
from filings_rag.harness import config as run_config
from filings_rag.harness import run as harness
from filings_rag.harness.prompts import get_judge_prompt, get_prompt
from filings_rag.ingest import build
from filings_rag.ingest.registry import get_pipeline
from filings_rag.pricing import embedding_cost
from filings_rag.retrieve.factory import make_retriever

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


@corpus_app.command("fetch")
def corpus_fetch(
    only: str = typer.Option("", help="Fetch just this doc_name"),
) -> None:
    """Download every resolved manifest filing, extract text, and upsert `documents`."""
    settings = get_settings()
    client = EdgarClient(settings.sec_user_agent, DATA / "edgar" / "cache")
    with (CORPUS / "companies.csv").open() as fh:
        tickers = {r["company"]: r["ticker"] for r in csv.DictReader(fh)}
    rows = [
        r for r in fetch.read_manifest(CORPUS / "manifest.csv") if not only or r.doc_name == only
    ]
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


@ingest_app.command("build")
def ingest_build(
    pipeline: str = typer.Argument(..., help="name@version, e.g. naive-fixed@1"),
    only: str = typer.Option("", help="Build just this doc_name"),
) -> None:
    """Chunk, embed and index every document for one pipeline version (resumable)."""
    settings = get_settings()
    p = get_pipeline(pipeline)
    embedder = VoyageEmbedder(settings.voyage_api_key)
    with psycopg.connect(settings.database_url) as conn:
        stats = build.build(conn, p, embedder, docs=[only] if only else None, log=typer.echo)
    cost = embedding_cost(embedder.name, stats.embed_tokens)
    typer.echo(
        f"{pipeline}: {stats.documents} documents ({stats.empty} empty), {stats.chunks:,} chunks, "
        f"{stats.embed_tokens:,} embedding tokens (${cost:.2f} at list)"
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


@app.command("run")
def run_cmd(
    config: str = typer.Argument(..., help="Run config, e.g. configs/p0-naive.toml"),
    limit: int | None = typer.Option(None, help="Only the first N questions (by id)"),
    resume: int | None = typer.Option(None, help="Continue this run id"),
) -> None:
    """Answer the question set with one config and record everything in `runs`."""
    cfg = run_config.load(Path(config))
    settings = get_settings()
    git = harness.git_info(ROOT)
    if git.dirty:
        typer.echo("warning: uncommitted changes; the recorded git SHA won't reproduce this run")
    with psycopg.connect(settings.database_url) as conn:
        table, embedder_name, dims = harness.pipeline_index(conn, cfg.pipeline)
        retriever = make_retriever(
            conn, cfg.retriever, table, make_embedder(embedder_name, dims, settings)
        )
        spec = cfg.backend_spec
        backend = make_backend(spec) if spec else None  # None: retrieval-only run
        prompt = get_prompt(cfg.generation["prompt"]) if cfg.generation else None
        if resume is not None:
            harness.check_resume(conn, resume, cfg)
            run_id = resume
        else:
            run_id = harness.create_run(conn, cfg, git)
        typer.echo(f"run {run_id}: {cfg.name} (config {cfg.hash})")
        s = harness.execute(
            conn, run_id, cfg, retriever, backend, prompt, git, limit=limit, log=typer.echo
        )
    u = s.usage
    typer.echo(
        f"run {run_id}: {s.answered} {'answered' if backend else 'retrieved'}, {s.failed} failed,"
        f" {s.skipped} already done"
        f"{' (stopped early)' if s.stopped_early else ''}{' — complete' if s.finished else ''}\n"
        f"  tokens: {u.input_tokens:,} in, {u.output_tokens:,} out, {u.cache_read_tokens:,} cache"
        f" read, {u.cache_write_tokens + u.cache_write_1h_tokens:,} cache write;"
        f" {s.embed_tokens:,} query-embedding\n"
        f"  API-equivalent cost: ${s.gen_cost + s.embed_cost:.4f}"
        f" (generation ${s.gen_cost:.4f}, embedding ${s.embed_cost:.6f});"
        f" backend reported ${s.reported_cost:.4f}"
    )
    if s.failed or s.stopped_early:
        raise typer.Exit(1)


@app.command("judge")
def judge_cmd(run_id: int = typer.Argument(..., help="Run to grade")) -> None:
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
        typer.echo(f"run {run_id}: judging with {cfg.judge_key}")
        s = judging.judge_run(
            conn,
            run_id,
            cfg.judge_key,
            make_backend(cfg.judge_spec),
            get_judge_prompt(cfg.judge["prompt"]),
            concurrency=cfg.execution["concurrency"],
            log=typer.echo,
        )
    typer.echo(
        f"run {run_id}: {s.judged} judged, {s.failed} failed, {s.skipped} already judged;"
        f" judge cost ${s.cost:.4f} (API-equivalent)"
    )
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
            text = report.render(summaries, retrieval_only=retrieval_only)
        except ValueError as e:  # unknown run, or a stored config that no longer loads
            typer.echo(f"cannot report: {e}", err=True)
            raise typer.Exit(1) from e
    if out:
        Path(out).write_text(text)
    typer.echo(text)


if __name__ == "__main__":
    app()
