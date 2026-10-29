import csv
from collections import Counter
from pathlib import Path

import psycopg
import typer

from filings_rag import db
from filings_rag.benchmark import financebench
from filings_rag.config import get_settings
from filings_rag.edgar import fetch, resolve
from filings_rag.edgar.client import EdgarClient
from filings_rag.embed.voyage import VoyageEmbedder
from filings_rag.eval import labels
from filings_rag.ingest import build
from filings_rag.ingest.registry import get_pipeline
from filings_rag.pricing import embedding_cost

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
        f"{pipeline}: {stats.documents} documents, {stats.chunks:,} chunks, "
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


if __name__ == "__main__":
    app()
