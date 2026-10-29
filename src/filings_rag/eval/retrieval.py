"""Retrieval metrics from stored evidence labels: did the retrieved chunks hold the evidence?

Per question, each FinanceBench evidence passage that has chunk labels for the run's pipeline
counts once. recall@k is the share of those passages with at least one labelled chunk in the
top k; hit@k is whether any passage was found in the top k; MRR uses the rank of the first
retrieved chunk that holds any evidence. Questions with no labelled passage are not scored.
"""

from dataclasses import dataclass
from typing import Any

import psycopg

from filings_rag.ingest.registry import get_pipeline


@dataclass(frozen=True)
class QuestionRetrieval:
    question_id: str
    found_at: list[int | None]  # per labelled passage: best rank of its chunks, or None

    @property
    def first_rank(self) -> int | None:
        ranks = [r for r in self.found_at if r is not None]
        return min(ranks) if ranks else None


def score(question_id: str, retrieved: list[int], labels: list[set[int]]) -> QuestionRetrieval:
    """`retrieved`: chunk ids in rank order. `labels`: the chunk ids of each evidence passage."""
    rank = {cid: i for i, cid in reversed(list(enumerate(retrieved, 1)))}
    found = [min((rank[c] for c in chunks if c in rank), default=None) for chunks in labels]
    return QuestionRetrieval(question_id, found)


def recall_at(qs: list[QuestionRetrieval], k: int) -> float:
    if not qs:
        return 0.0
    return sum(
        sum(r is not None and r <= k for r in q.found_at) / len(q.found_at) for q in qs
    ) / len(qs)


def hit_at(qs: list[QuestionRetrieval], k: int) -> float:
    return sum((q.first_rank or k + 1) <= k for q in qs) / len(qs) if qs else 0.0


def mrr(qs: list[QuestionRetrieval]) -> float:
    return sum(1 / q.first_rank for q in qs if q.first_rank) / len(qs) if qs else 0.0


def load(
    conn: psycopg.Connection[Any], run_id: int, pipeline_ref: str
) -> tuple[list[QuestionRetrieval], int]:
    """(scored questions, number of answered questions that have no labelled evidence)."""
    p = get_pipeline(pipeline_ref)
    labels: dict[str, dict[int, set[int]]] = {}
    for qid, idx, cid in conn.execute(
        "SELECT l.question_id, l.evidence_idx, l.chunk_id FROM evidence_labels l"
        " JOIN run_questions r ON r.question_id = l.question_id AND r.run_id = %s"
        " WHERE l.pipeline = %s AND l.version = %s",
        (run_id, p.name, p.version),
    ):
        labels.setdefault(qid, {}).setdefault(idx, set()).add(cid)
    scored, unlabelled = [], 0
    for qid, retrieved in conn.execute(
        "SELECT question_id, retrieved FROM run_questions"
        " WHERE run_id = %s AND error IS NULL ORDER BY question_id",
        (run_id,),
    ):
        if qid not in labels:
            unlabelled += 1
            continue
        ids = [int(h["chunk_id"]) for h in retrieved]
        scored.append(score(qid, ids, list(labels[qid].values())))
    return scored, unlabelled
