import typer

from filings_rag import db
from filings_rag.config import get_settings

app = typer.Typer(no_args_is_help=True, help="Filings RAG Lab")
db_app = typer.Typer(no_args_is_help=True, help="Database commands")
app.add_typer(db_app, name="db")


@db_app.command("migrate")
def db_migrate() -> None:
    """Apply pending SQL migrations."""
    applied = db.migrate(get_settings().database_url)
    typer.echo("\n".join(f"applied {n}" for n in applied) if applied else "up to date")


if __name__ == "__main__":
    app()
