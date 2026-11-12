"""Production feedback as eval cases (P4 PR 10): `prod-feedback@1`, its own set, never part of
the open-subset headline.

1. `scripts/feedback_pull.sh` restores the newest frl_app backup into a throwaway container and
   writes the thumbs-down rows (smoke traffic excluded) to `data/feedback/<date>.jsonl`. Those
   files hold what strangers asked and are never committed.
2. `frl bench from-feedback <pulled.jsonl> <query id>...` appends a *draft* case per chosen
   query to `corpus/prod-feedback@1.jsonl`: the question as asked, the filing the scope picked
   when it picked exactly one, and an empty gold answer.
3. A person fills in the gold by hand (answer, company, doc_name, justification and, when there
   is one, the evidence passage), checking it against the filing. No model writes gold.
4. `frl bench load-feedback` loads the set, refusing while any case is still a draft, and
   `frl run configs/p4-prod-feedback.toml` replays it through the production pipeline.
"""

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from filings_rag.benchmark.synth import SynthQuestion

SET = "prod-feedback@1"
QUESTION_TYPE = "prod-feedback"
ID_PREFIX = "prodfeedback1/"
SMOKE_TAG = "smoke"


def case_id(query_id: str) -> str:
    return ID_PREFIX + query_id.replace("-", "")[:12]


def read_pulled(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def pick(rows: Iterable[dict[str, Any]], prefixes: list[str]) -> list[dict[str, Any]]:
    """The pulled rows whose query id starts with each prefix (8+ hex characters), in the order
    asked. A prefix matching no row, or more than one, is an error."""
    rows = list(rows)
    chosen = []
    for prefix in prefixes:
        p = prefix.lower().replace("-", "")
        if len(p) < 8:
            raise ValueError(f"query id prefix {prefix!r} is shorter than 8 characters")
        hits = [r for r in rows if str(r["query_id"]).replace("-", "").startswith(p)]
        if len(hits) != 1:
            raise ValueError(f"query id prefix {prefix!r} matches {len(hits)} pulled rows")
        chosen.append(hits[0])
    return chosen


def draft(row: dict[str, Any]) -> SynthQuestion:
    """A draft case from one pulled thumbs-down. The gold stays empty for a person to fill."""
    if SMOKE_TAG in (row.get("tags") or []):
        raise ValueError(f"query {row['query_id']} is smoke traffic, not a user's feedback")
    if row.get("rating") != "down":
        raise ValueError(f"query {row['query_id']} was rated {row.get('rating')!r}, not down")
    docs = (row.get("scope") or {}).get("docs") or []
    asked = str(row.get("asked_at", ""))[:10]
    return SynthQuestion(
        id=case_id(str(row["query_id"])),
        benchmark=SET,
        company="",
        doc_name=docs[0] if len(docs) == 1 else "",
        question_type=QUESTION_TYPE,
        question_reasoning=None,
        question=row["question"],
        answer="",
        justification=(
            f"[source: production query {row['query_id']}, asked {asked}, served"
            f" {str(row.get('git_sha', ''))[:8]}, rated down]"
        ),
        evidence=[],
    )


def problems(q: SynthQuestion) -> list[str]:
    """What still stops a case from loading; empty when it's complete."""
    out = []
    if q.benchmark != SET or not q.id.startswith(ID_PREFIX):
        out.append(f"not a {SET} case")
    for name in ("answer", "company", "doc_name"):
        if not str(getattr(q, name)).strip():
            out.append(f"no {name}")
    if any(not e.get("doc_name") for e in q.evidence):
        out.append("evidence without a doc_name")
    return out


def merge(
    existing: list[SynthQuestion], new: list[SynthQuestion]
) -> tuple[list[SynthQuestion], int]:
    """Append the new drafts, leaving any case already in the file (and its hand-written gold)
    untouched. Returns the merged list and how many were added."""
    have = {q.id for q in existing}
    added = []
    for q in new:
        if q.id not in have:
            have.add(q.id)
            added.append(q)
    return existing + added, len(added)
