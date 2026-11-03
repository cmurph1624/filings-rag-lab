"""Synthetic question sets (P3 PR 8): their own sets, never folded into the open subset.

`synth-facts@1`: templated from XBRL and `metrics@1`. One question per (company, fiscal year,
metric), worded in the vocabulary the rules router knows, with gold computed in code from the
facts as filed in that year's 10-K. A question is skipped unless every input is as filed. No
judge is needed: the fact route's headline value is compared with the gold number
(`route.evaluate.fact_route_accuracy`).

`synth-prose@1`: a model (`synth-prose@1` prompt) writes one question from a sampled chunk of
a 10-K; the chunk is the question's evidence, so recall@k works as for the open subset. Rules
drop bad generations (`check_prose`); a hand-checked sample is reported in
docs/results/p3-synth.md.

Both are written to `corpus/<set>.jsonl`, which is committed: the prose questions come from a
model and cannot be regenerated identically. `load` puts a file's questions into the database.
"""

import json
import random
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from filings_rag.edgar.resolve import parse_doc_name
from filings_rag.facts import metrics as M
from filings_rag.facts.concepts import ConceptMap
from filings_rag.facts.lookup import FactLookup

FACTS_SET = "synth-facts@1"
PROSE_SET = "synth-prose@1"
SEED = 20261102

# metric -> the phrase the question uses. Each phrase is matched by the rules router
# (tests/test_synth.py checks every one), so the set measures scope, facts and answering, not
# the router's vocabulary.
FACT_METRICS = {
    "net_income": "net income",
    "total_assets": "total assets",
    "current_assets": "total current assets",
    "current_liabilities": "total current liabilities",
    "inventory": "inventory",
    "accounts_receivable": "accounts receivable",
    "accounts_payable": "accounts payable",
    "ppe_net": "net property, plant and equipment",
    "cogs": "cost of goods sold",
    "cash": "cash and cash equivalents",
    "capex": "capital expenditure",
    "cfo": "cash flow from operating activities",
    "dividends_paid": "cash dividends paid",
    "working_capital": "working capital",
    "current_ratio": "current ratio",
    "quick_ratio": "quick ratio",
    "gross_margin": "gross margin",
    "operating_margin": "operating margin",
    "net_margin": "net profit margin",
    "ebitda": "EBITDA",
    "ebitda_margin": "EBITDA margin",
    "fcf": "free cash flow",
    "capex_pct_revenue": "capex as a % of revenue",
    "effective_tax_rate": "effective tax rate",
    "payout_ratio": "dividend payout ratio",
    "roa": "return on assets",
    "asset_turnover": "asset turnover",
    "inventory_turnover": "inventory turnover",
    "dpo": "days payable outstanding",
}
UNIT_HINT = {
    "usd": "in USD millions",
    "pct": "as a percentage rounded to one decimal place",
    "ratio": "rounded to two decimal places",
    "days": "in days, rounded to two decimal places",
}
QUESTION_TYPE = "synthetic"


@dataclass(frozen=True)
class SynthQuestion:
    id: str
    benchmark: str
    company: str
    doc_name: str
    question_type: str
    question_reasoning: str | None
    question: str
    answer: str
    justification: str | None
    evidence: list[dict[str, Any]]


def write_jsonl(path: Path, questions: Iterable[SynthQuestion]) -> int:
    rows = [json.dumps(asdict(q), sort_keys=True, ensure_ascii=False) for q in questions]
    path.write_text("\n".join(rows) + "\n")
    return len(rows)


def read_jsonl(path: Path) -> list[SynthQuestion]:
    return [
        SynthQuestion(**json.loads(line)) for line in path.read_text().splitlines() if line.strip()
    ]


def load(conn: psycopg.Connection[Any], questions: list[SynthQuestion]) -> int:
    """Upsert questions by id. Fails if a gold filing isn't fetched, or an id belongs to the
    open subset."""
    docs = {r[0] for r in conn.execute("SELECT doc_name FROM documents")}
    if missing := sorted({q.doc_name for q in questions} - docs):
        raise ValueError(f"gold filings not fetched: {missing[:5]}")
    ids = [q.id for q in questions]
    own = {
        r[0]
        for r in conn.execute(
            "SELECT id FROM questions WHERE id = ANY(%s) AND benchmark = 'financebench-open'",
            (ids,),
        )
    }
    if own:
        raise ValueError(f"ids belong to the open subset: {sorted(own)[:5]}")
    with conn.transaction():
        for q in questions:
            conn.execute(
                """INSERT INTO questions
                   (id, benchmark, company, doc_name, question_type, question_reasoning,
                    question, answer, justification, evidence)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (id) DO UPDATE SET benchmark = EXCLUDED.benchmark,
                     company = EXCLUDED.company, doc_name = EXCLUDED.doc_name,
                     question_type = EXCLUDED.question_type,
                     question_reasoning = EXCLUDED.question_reasoning,
                     question = EXCLUDED.question, answer = EXCLUDED.answer,
                     justification = EXCLUDED.justification, evidence = EXCLUDED.evidence""",
                (
                    q.id,
                    q.benchmark,
                    q.company,
                    q.doc_name,
                    q.question_type,
                    q.question_reasoning,
                    q.question,
                    q.answer,
                    q.justification,
                    Jsonb(q.evidence),
                ),
            )
    return len(questions)


def label_year(doc_name: str, offset: int, benchmark_docs: set[str]) -> int:
    """The fiscal year a filing goes by in a question: FinanceBench's label for its own
    documents, the company's year shifted by `label_offset` for the rest (as the scope does)."""
    return parse_doc_name(doc_name).fiscal_year + (0 if doc_name in benchmark_docs else offset)


# --- synth-facts@1 ----------------------------------------------------------------------------


@dataclass(frozen=True)
class FactCandidate:
    company: str
    doc_name: str
    year: int
    metric: str
    unit: str
    value: float
    formula: str


def gold_text(unit: str, value: float) -> str | None:
    """The gold answer as written in the question's unit; None when it rounds to zero (an
    uninformative question)."""
    if unit == "usd":
        n = round(value / 1e6)
        return str(n) if n else None
    if unit == "pct":
        x = round(100 * value, 1)
        return f"{x:.1f}%" if x else None
    if unit in ("ratio", "days"):
        x = round(value, 2)
        return f"{x:.2f}" if x else None
    return None


def question_text(company: str, year: int, metric: str, unit: str) -> str:
    return f"What is the FY{year} {FACT_METRICS[metric]} for {company}, {UNIT_HINT[unit]}?"


def fact_candidates(
    conn: psycopg.Connection[Any],
    cmap: ConceptMap,
    offsets: dict[int, int],
    names: dict[int, str],
    corpus: str,
    corpus_version: int,
) -> list[FactCandidate]:
    """Every (10-K, metric) in the corpus whose gold is computable with all inputs as filed."""
    benchmark = {r[0] for r in conn.execute("SELECT doc_name FROM benchmark_docs")}
    docs = conn.execute(
        "SELECT d.doc_name, d.cik, d.period_end FROM documents d"
        " JOIN corpus_documents cd ON cd.accession = d.accession"
        " AND cd.corpus = %s AND cd.version = %s"
        " WHERE d.form_type = '10-K' AND d.period_end IS NOT NULL ORDER BY d.doc_name",
        (corpus, corpus_version),
    ).fetchall()
    conn.commit()
    out: list[FactCandidate] = []
    for doc_name, cik, end in docs:
        if cik not in names:
            continue
        year = label_year(doc_name, offsets.get(cik, 0), benchmark)
        lookup = FactLookup(conn, cmap, doc_name, offsets)
        if lookup.period_end((year, None)) != end:
            continue  # another filing goes by this label
        for metric in FACT_METRICS:
            try:
                v = M.compute(lookup, metric, (year, None))
            except (M.MissingInput, ValueError, ZeroDivisionError):
                continue
            if not all(f.as_filed for f in v.facts) or gold_text(v.unit, v.value) is None:
                continue
            out.append(
                FactCandidate(names[cik], doc_name, year, metric, v.unit, v.value, v.formula)
            )
    return out


def select_facts(
    cands: list[FactCandidate], n: int, *, per_company: int, per_metric: int, seed: int = SEED
) -> list[FactCandidate]:
    """A seeded sample of about n candidates with no company or metric dominating."""
    rng = random.Random(seed)
    pool = sorted(cands, key=lambda c: (c.doc_name, c.metric))
    rng.shuffle(pool)
    by_company: Counter[str] = Counter()
    by_metric: Counter[str] = Counter()
    chosen: list[FactCandidate] = []
    for c in pool:
        if len(chosen) >= n:
            break
        if by_company[c.company] >= per_company or by_metric[c.metric] >= per_metric:
            continue
        by_company[c.company] += 1
        by_metric[c.metric] += 1
        chosen.append(c)
    return sorted(chosen, key=lambda c: (c.doc_name, c.metric))


def fact_question(c: FactCandidate) -> SynthQuestion:
    gold = gold_text(c.unit, c.value)
    assert gold is not None
    return SynthQuestion(
        id=f"synthfacts1/{c.doc_name}/{c.metric}",
        benchmark=FACTS_SET,
        company=c.company,
        doc_name=c.doc_name,
        question_type=QUESTION_TYPE,
        question_reasoning="metric",
        question=question_text(c.company, c.year, c.metric, c.unit),
        answer=gold,
        justification=f"{c.metric} = {c.formula} (metrics@1, as filed in {c.doc_name})",
        evidence=[],
    )


# --- synth-prose@1 ----------------------------------------------------------------------------

SECTION_OK = re.compile(r"^item\s*(1|1a|1c|7|7a)\b", re.IGNORECASE)
MIN_WORDS, MAX_WORDS = 120, 450
MAX_DIGIT_SHARE = 0.10  # a chunk that is mostly numbers is a table; its question would be a lookup
META = re.compile(
    r"\b(excerpt|passage|the text|above|following (?:section|paragraph)|this section|"
    r"the document|the filing says)\b",
    re.IGNORECASE,
)
MAX_COPIED_WORDS = 8  # a question that copies this many words in a row from the chunk is a quote


@dataclass(frozen=True)
class ProseCandidate:
    chunk_id: int
    ord: int
    doc_name: str
    company: str
    year: int
    page: int
    section: str
    text: str


def is_prose_chunk(section: str | None, text: str) -> bool:
    words = text.split()
    if not section or not SECTION_OK.match(section) or not MIN_WORDS <= len(words) <= MAX_WORDS:
        return False
    chars = [ch for ch in text if not ch.isspace()]
    return sum(ch.isdigit() for ch in chars) / max(len(chars), 1) <= MAX_DIGIT_SHARE


def prose_candidates(
    rows: Iterable[tuple[Any, ...]],
    offsets: dict[int, int],
    benchmark_docs: set[str],
    n: int,
    *,
    per_doc: int = 3,
    seed: int = SEED,
) -> list[ProseCandidate]:
    """From (chunk_id, ord, doc_name, cik, company, page, section, text) rows, a seeded sample
    of n prose chunks with at most `per_doc` from one filing."""
    pool = [
        ProseCandidate(
            int(cid),
            int(o),
            doc,
            company,
            label_year(doc, offsets.get(cik, 0), benchmark_docs),
            int(page),
            str(section),
            str(text),
        )
        for cid, o, doc, cik, company, page, section, text in rows
        if is_prose_chunk(section, text)
    ]
    pool.sort(key=lambda c: (c.doc_name, c.ord))
    random.Random(seed).shuffle(pool)
    per: Counter[str] = Counter()
    chosen: list[ProseCandidate] = []
    for c in pool:
        if len(chosen) >= n:
            break
        if per[c.doc_name] >= per_doc:
            continue
        per[c.doc_name] += 1
        chosen.append(c)
    return chosen


_JSON = re.compile(r"\{.*\}", re.DOTALL)
_WORD = re.compile(r"[a-z0-9$%.]+")


def _words(s: str) -> list[str]:
    return _WORD.findall(s.lower())


def copied_run(question: str, chunk: str) -> int:
    """The longest run of consecutive words the question shares with the chunk."""
    q, c = _words(question), _words(chunk)
    grams = {tuple(c[i : i + 4]) for i in range(len(c) - 3)}
    best = 0
    for i in range(len(q)):
        n = 0
        while i + n + 4 <= len(q) and tuple(q[i + n : i + n + 4]) in grams:
            n += 1
        best = max(best, n + 3 if n else 0)
    return best


def check_prose(
    reply: str, cand: ProseCandidate, names: list[str]
) -> tuple[tuple[str, str] | None, str]:
    """((question, answer), "") for a usable generation, else (None, why it was dropped)."""
    m = _JSON.search(reply)
    if not m:
        return None, "no JSON"
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None, "bad JSON"
    if not isinstance(data, dict):
        return None, "bad JSON"
    if data.get("skip"):
        return None, "model skipped"
    q, a = str(data.get("question") or "").strip(), str(data.get("answer") or "").strip()
    if not q or not a:
        return None, "missing question or answer"
    if not q.endswith("?") or not 8 <= len(q.split()) <= 60:
        return None, "not a question of 8-60 words"
    if len(a) > 700:
        return None, "answer too long"
    if not any(n.lower() in q.lower() for n in names):
        return None, "does not name the company"
    years = set(re.findall(r"FY(\d{4})", q))
    if years != {str(cand.year)}:
        return None, "does not name exactly the filing's fiscal year"
    if META.search(q):
        return None, "refers to the excerpt"
    if copied_run(q, cand.text) >= MAX_COPIED_WORDS:
        return None, "copies the excerpt"
    return (q, a), ""


def prose_question(cand: ProseCandidate, question: str, answer: str) -> SynthQuestion:
    return SynthQuestion(
        id=f"synthprose1/{cand.doc_name}/{cand.ord}",
        benchmark=PROSE_SET,
        company=cand.company,
        doc_name=cand.doc_name,
        question_type=QUESTION_TYPE,
        question_reasoning="prose",
        question=question,
        answer=answer,
        justification=f"written from chunk {cand.ord} of {cand.doc_name}, page {cand.page}"
        f" ({cand.section})",
        evidence=[{"evidence_text": cand.text, "doc_name": cand.doc_name}],
    )
