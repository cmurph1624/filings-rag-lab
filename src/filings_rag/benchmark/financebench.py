"""FinanceBench open subset (150 questions), pinned to one upstream commit."""

import json
from pathlib import Path
from typing import Any

import httpx
import psycopg
from psycopg.types.json import Jsonb

# Pinned so a silent upstream edit can never move scores. Bump deliberately.
COMMIT = "cc39aeb4afdf33909ee1412188bf89035950c2eb"
BASE = f"https://raw.githubusercontent.com/patronus-ai/financebench/{COMMIT}"
QUESTIONS_FILE = "financebench_open_source.jsonl"
DOCS_FILE = "financebench_document_information.jsonl"
PDF_URL = BASE + "/pdfs/{doc_name}.pdf"


def download(dest: Path) -> None:
    """Fetch the two JSONL files into `dest` (skips files already present)."""
    dest.mkdir(parents=True, exist_ok=True)
    for name in (QUESTIONS_FILE, DOCS_FILE):
        path = dest / name
        if path.exists():
            continue
        resp = httpx.get(f"{BASE}/data/{name}", follow_redirects=True, timeout=60)
        resp.raise_for_status()
        path.write_bytes(resp.content)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load(conn: psycopg.Connection[Any], src: Path) -> tuple[int, int]:
    """Upsert docs referenced by the questions, then the questions. Returns (docs, questions)."""
    questions = read_jsonl(src / QUESTIONS_FILE)
    referenced = {q["doc_name"] for q in questions}
    docs = [d for d in read_jsonl(src / DOCS_FILE) if d["doc_name"] in referenced]
    missing = referenced - {d["doc_name"] for d in docs}
    if missing:
        raise ValueError(f"questions reference unknown docs: {sorted(missing)}")
    with conn.transaction():
        for d in docs:
            conn.execute(
                """INSERT INTO benchmark_docs
                   (doc_name, company, gics_sector, doc_type, doc_period, doc_link)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (doc_name) DO UPDATE SET company = EXCLUDED.company,
                     gics_sector = EXCLUDED.gics_sector, doc_type = EXCLUDED.doc_type,
                     doc_period = EXCLUDED.doc_period, doc_link = EXCLUDED.doc_link""",
                (
                    d["doc_name"],
                    d["company"],
                    d.get("gics_sector"),
                    d["doc_type"],
                    int(d["doc_period"]),
                    d.get("doc_link"),
                ),
            )
        for q in questions:
            conn.execute(
                """INSERT INTO questions
                   (id, company, doc_name, question_type, question_reasoning, question,
                    answer, justification, evidence)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (id) DO UPDATE SET company = EXCLUDED.company,
                     doc_name = EXCLUDED.doc_name, question_type = EXCLUDED.question_type,
                     question_reasoning = EXCLUDED.question_reasoning,
                     question = EXCLUDED.question, answer = EXCLUDED.answer,
                     justification = EXCLUDED.justification, evidence = EXCLUDED.evidence""",
                (
                    q["financebench_id"],
                    q["company"],
                    q["doc_name"],
                    q["question_type"],
                    q.get("question_reasoning"),
                    q["question"],
                    q["answer"],
                    q.get("justification"),
                    Jsonb(q["evidence"]),
                ),
            )
    return len(docs), len(questions)
