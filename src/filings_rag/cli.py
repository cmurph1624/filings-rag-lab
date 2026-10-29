from collections import Counter
from pathlib import Path

import psycopg
import typer

from filings_rag import db
from filings_rag.benchmark import financebench
from filings_rag.config import get_settings
from filings_rag.edgar import resolve
from filings_rag.edgar.client import EdgarClient

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


@db_app.command("migrate")
def db_migrate() -> None:
    """Apply pending SQL migrations."""
    applied = db.migrate(get_settings().database_url)
    typer.echo("\n".join(f"applied {n}" for n in applied) if applied else "up to date")


@bench_app.command("pull")
def bench_pull() -> None:
    """Download the pinned FinanceBench open subset and load it into Postgres."""
    src = DATA / "financebench"
    financebench.download(src)
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


if __name__ == "__main__":
    app()
