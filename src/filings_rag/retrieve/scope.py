"""Query scoping: narrow retrieval to the filings a question is about.

`[retriever.scope] mode`:
- "none": the whole corpus (the P0 behaviour).
- "rules": the company from its name, aliases or ticker; fiscal years and quarters from regexes.
- "llm": a small model extracts the same fields as JSON (cached per question in `query_scopes`).
- "oracle": the question's own filing. A labelled upper bound, never a headline number.

Mentions select filings by their label, the year and period in the FinanceBench-style
`doc_name` (so FinanceBench's own labels for question filings, Ulta included):
- annual filings (10-K, 8-K, Q4 releases) match a mentioned year y when
  y <= label year <= y + lookahead (a 10-K repeats the two years before it);
- quarterly filings (10-Q, Q1-Q3 releases) match only a mentioned (year, quarter).
A quarter's year counts as a mentioned year too. If nothing matches, the scope falls back to
all of the company's filings; with no company found, to the whole corpus. The level reached
is recorded, so the fallback rate can be reported.
"""

import csv
import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from filings_rag.backends.base import ModelBackend
from filings_rag.backends.factory import make_backend
from filings_rag.edgar.resolve import parse_doc_name
from filings_rag.pricing import model_cost

ANNUAL = ("FY", "Q4")

_Y4 = r"((?:19|20)\d{2})"
FY = re.compile(r"\bFY\s*'?(\d{4}|\d{2})(?!\d)", re.IGNORECASE)
PLAIN_YEAR = re.compile(r"(?<![\d$])" + _Y4 + r"(?!\d)")
QUARTER_FIRST = re.compile(
    r"\bQ([1-4])\s*(?:of\s+)?(?:fiscal\s+(?:year\s+)?|FY\s*)?'?" + _Y4 + r"(?!\d)", re.IGNORECASE
)
QUARTER_FY2 = re.compile(r"\bQ([1-4])\s*(?:of\s+)?FY\s*'?(\d{2})(?!\d)", re.IGNORECASE)
YEAR_FIRST = re.compile(r"(?:\bFY\s*)?" + _Y4 + r"\s*Q([1-4])\b", re.IGNORECASE)
ORDINAL = re.compile(
    r"\b(first|second|third|fourth)\s+(?:fiscal\s+)?quarter\s+(?:of\s+)?"
    r"(?:fiscal\s+(?:year\s+)?|FY\s*)?" + _Y4,
    re.IGNORECASE,
)
HALF = re.compile(r"\bH([12])\s*(?:of\s+)?(?:FY\s*)?" + _Y4, re.IGNORECASE)
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4}


def _year(s: str) -> int:
    return int(s) if len(s) == 4 else 2000 + int(s)


def parse_periods(text: str) -> tuple[list[int], list[tuple[int, int]]]:
    """(fiscal years, (year, quarter) pairs) named in a question, sorted and de-duplicated."""
    quarters: set[tuple[int, int]] = set()
    for m in QUARTER_FIRST.finditer(text):
        quarters.add((int(m[2]), int(m[1])))
    for m in QUARTER_FY2.finditer(text):
        quarters.add((_year(m[2]), int(m[1])))
    for m in YEAR_FIRST.finditer(text):
        quarters.add((int(m[1]), int(m[2])))
    for m in ORDINAL.finditer(text):
        quarters.add((int(m[2]), ORDINALS[m[1].lower()]))
    for m in HALF.finditer(text):
        quarters.add((int(m[2]), 2 * int(m[1])))  # H1 ends with Q2, H2 with Q4
    years = {_year(m[1]) for m in FY.finditer(text)}
    years |= {int(m[1]) for m in PLAIN_YEAR.finditer(text)}
    years |= {y for y, _ in quarters}
    return sorted(years), sorted(quarters)


@dataclass(frozen=True)
class Company:
    name: str
    ticker: str
    aliases: tuple[str, ...]


def company_patterns(companies: Iterable[Company]) -> list[tuple[str, re.Pattern[str]]]:
    """Names and aliases match case-insensitively; tickers only in capitals (KO, BA, FL)."""
    out = []
    for c in companies:
        names = "|".join(re.escape(n) for n in (c.name, *c.aliases) if n)
        out.append((c.name, re.compile(rf"(?<!\w)(?:{names})(?!\w)", re.IGNORECASE)))
        if c.ticker:
            out.append((c.name, re.compile(rf"(?<!\w){re.escape(c.ticker)}(?!\w)")))
    return out


def find_company(text: str, patterns: list[tuple[str, re.Pattern[str]]]) -> str | None:
    """The company mentioned first (longest match on a tie), or None."""
    best: tuple[int, int, str] | None = None
    for name, pat in patterns:
        m = pat.search(text)
        if m and (best is None or (m.start(), -len(m[0])) < (best[0], best[1])):
            best = (m.start(), -len(m[0]), name)
    return best[2] if best else None


@dataclass(frozen=True)
class Mentions:
    company: str | None
    years: list[int] = field(default_factory=list)
    quarters: list[tuple[int, int]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {**asdict(self), "quarters": [list(q) for q in self.quarters]}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "Mentions":
        return cls(
            d.get("company"),
            sorted({int(y) for y in d.get("years", [])}),
            sorted({(int(y), int(q)) for y, q in d.get("quarters", [])}),
        )


def rules_mentions(text: str, patterns: list[tuple[str, re.Pattern[str]]]) -> Mentions:
    years, quarters = parse_periods(text)
    return Mentions(find_company(text, patterns), years, quarters)


@dataclass(frozen=True)
class DocLabel:
    id: int
    doc_name: str
    company: str
    year: int
    period: str  # FY | Q1..Q4

    @classmethod
    def from_doc_name(cls, doc_id: int, doc_name: str, company: str) -> "DocLabel":
        ref = parse_doc_name(doc_name)
        return cls(doc_id, doc_name, company, ref.fiscal_year, ref.fiscal_period)

    def matches(self, m: Mentions, lookahead: int) -> bool:
        if self.period in ANNUAL:
            return any(y <= self.year <= y + lookahead for y in m.years)
        return (self.year, int(self.period[1])) in m.quarters


def select(labels: list[DocLabel], m: Mentions, lookahead: int) -> tuple[list[DocLabel], str]:
    """(filings in scope, level): level is "filings", "company" (fallback) or "corpus"."""
    own = [d for d in labels if d.company == m.company]
    if not own:
        return labels, "corpus"
    hit = [d for d in own if d.matches(m, lookahead)]
    return (hit, "filings") if hit else (own, "company")


# --- configuration ---------------------------------------------------------------------------

# Every option with its default, so the config hash covers what was used. "none" adds nothing,
# so configs without a scope keep their hashes.
SCOPE_DEFAULTS: dict[str, dict[str, Any]] = {
    "none": {},
    "rules": {"lookahead": 0},
    "oracle": {},
    "llm": {
        "lookahead": 0,
        "backend": "claude-cli",
        "model": "claude-haiku-5-5",
        "prompt": "scope@1",
    },
}


def resolve_scope(spec: dict[str, Any] | None) -> dict[str, Any] | None:
    """The [retriever.scope] table with defaults filled in; None for no scoping."""
    if not spec:
        return None
    mode = spec.get("mode", "none")
    if mode not in SCOPE_DEFAULTS:
        raise ValueError(f"unknown scope mode {mode!r}; known: {list(SCOPE_DEFAULTS)}")
    opts = {k: v for k, v in spec.items() if k != "mode"}
    unknown = opts.keys() - SCOPE_DEFAULTS[mode].keys()
    if unknown:
        raise ValueError(f"unknown {mode} scope options: {sorted(unknown)}")
    if mode == "none":
        return None
    resolved = {"mode": mode, **(SCOPE_DEFAULTS[mode] | opts)}
    if resolved.get("lookahead", 0) < 0:
        raise ValueError("scope lookahead must be 0 or more")
    if mode == "llm":
        get_scope_prompt(resolved["prompt"])
    return resolved


# --- the llm mode's prompt (versioned: never edit one in place, add scope@n+1) ----------------


@dataclass(frozen=True)
class ScopePrompt:
    system: str
    template: str  # {companies}, {question}

    def render(self, question: str, companies: list[str]) -> str:
        return self.template.format(companies="; ".join(companies), question=question)


SCOPE_V1 = ScopePrompt(
    system=(
        "You turn a question about a US public company's SEC filings into search filters. "
        "Reply with one JSON object and nothing else."
    ),
    template=(
        "Companies in the collection: {companies}\n\n"
        "Question: {question}\n\n"
        'Reply with {{"company": ..., "years": [...], "quarters": [...]}} where:\n'
        "- company is exactly one name from the list, or null if the question names none;\n"
        "- years are the fiscal years (4-digit integers) whose filings would hold the answer: "
        "the years the question names, plus the year before any year it asks for a forecast, "
        "outlook or guidance about;\n"
        "- quarters are [fiscal year, quarter number] pairs for any fiscal quarter or half "
        "the question names (a first half is quarter 2).\n"
        "Use fiscal years as the question states them. Leave a list empty rather than guess."
    ),
)
SCOPE_PROMPTS: dict[str, ScopePrompt] = {"scope@1": SCOPE_V1}


def get_scope_prompt(ref: str) -> ScopePrompt:
    if ref not in SCOPE_PROMPTS:
        raise ValueError(f"unknown scope prompt {ref!r}; known: {sorted(SCOPE_PROMPTS)}")
    return SCOPE_PROMPTS[ref]


JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def parse_llm_mentions(text: str, companies: list[str]) -> Mentions:
    """The model's JSON reply as Mentions; a company not in the list counts as none."""
    m = JSON_OBJECT.search(text)
    if m is None:
        raise ValueError(f"scope reply has no JSON object: {text[:200]!r}")
    data = json.loads(m[0])
    company = data.get("company")
    by_lower = {c.lower(): c for c in companies}
    name = by_lower.get(str(company).lower()) if company else None
    return Mentions.from_json(
        {"company": name, "years": data.get("years") or [], "quarters": data.get("quarters") or []}
    )


# --- scoping a question ---------------------------------------------------------------------


@dataclass(frozen=True)
class Scope:
    documents: list[int]  # `documents` ids to search, a subset of the corpus
    record: dict[str, Any]  # stored on the run row: mode, mentions, level, doc names
    aux: dict[str, Any] | None = None  # {model, usage, cost_usd, cached} when a model was used


class Scoper:
    def __init__(
        self,
        conn: psycopg.Connection[Any],
        spec: dict[str, Any],
        labels: list[DocLabel],
        companies: list[Company],
        backend: ModelBackend | None = None,
    ) -> None:
        self._conn = conn
        self.spec = spec
        self.mode = spec["mode"]
        self._labels = labels
        self._companies = companies
        self._patterns = company_patterns(companies)
        self._backend = backend
        if self.mode == "llm":
            if backend is None:
                raise ValueError("llm scope needs a backend")
            self._prompt = get_scope_prompt(spec["prompt"])
            self.key = f"{spec['backend']}:{spec['model']}:{spec['prompt']}"

    def scope(self, question_id: str, question: str) -> Scope:
        aux = None
        if self.mode == "oracle":
            row = self._conn.execute(
                "SELECT doc_name FROM questions WHERE id = %s", (question_id,)
            ).fetchone()
            gold = row[0] if row else None
            docs = [d for d in self._labels if d.doc_name == gold]
            level = "oracle" if docs else "corpus"
            docs = docs or self._labels
            mentions: dict[str, Any] = {}
        else:
            if self.mode == "llm":
                m, aux = self._llm(question_id, question)
            else:
                m = rules_mentions(question, self._patterns)
            docs, level = select(self._labels, m, self.spec.get("lookahead", 0))
            mentions = m.to_json()
        record = {"mode": self.mode, **mentions, "level": level}
        record["docs"] = [d.doc_name for d in docs] if level != "corpus" else []
        return Scope([d.id for d in docs], record, aux)

    def _llm(self, question_id: str, question: str) -> tuple[Mentions, dict[str, Any]]:
        row = self._conn.execute(
            "SELECT mentions, model, usage, cost_usd FROM query_scopes"
            " WHERE question_id = %s AND scope_key = %s",
            (question_id, self.key),
        ).fetchone()
        self._conn.commit()
        if row is not None:
            mentions, model, usage, cost = row
            aux = {"model": model, "usage": usage, "cost_usd": cost, "cached": True}
            return Mentions.from_json(mentions), aux
        assert self._backend is not None
        names = [c.name for c in self._companies]
        result = self._backend.generate(self._prompt.system, self._prompt.render(question, names))
        cost = model_cost(result.model, result.usage)
        m = parse_llm_mentions(result.text, names)
        usage = asdict(result.usage)
        self._conn.execute(
            "INSERT INTO query_scopes (question_id, scope_key, mentions, model, usage, cost_usd)"
            " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (question_id, self.key, Jsonb(m.to_json()), result.model, Jsonb(usage), cost),
        )
        self._conn.commit()
        return m, {"model": result.model, "usage": usage, "cost_usd": cost, "cached": False}


def read_companies(path: Path) -> tuple[list[Company], dict[int, str]]:
    """(companies with aliases, cik -> company) from corpus/companies.csv."""
    with path.open() as fh:
        rows = list(csv.DictReader(fh))
    companies = [
        Company(r["company"], r["ticker"], tuple(a for a in r.get("aliases", "").split(";") if a))
        for r in rows
    ]
    return companies, {int(r["cik"]): r["company"] for r in rows}


def make_scoper(
    conn: psycopg.Connection[Any],
    spec: dict[str, Any] | None,
    documents: list[int],
    companies_csv: Path,
) -> Scoper | None:
    """A scoper over the corpus's documents, or None when the config doesn't scope."""
    resolved = resolve_scope(spec)
    if resolved is None:
        return None
    companies, by_cik = read_companies(companies_csv)
    rows = conn.execute(
        "SELECT id, doc_name, cik FROM documents WHERE id = ANY(%s) ORDER BY id", (documents,)
    ).fetchall()
    conn.commit()
    unknown = sorted({cik for _, _, cik in rows} - by_cik.keys())
    if unknown:
        raise ValueError(f"CIKs {unknown} are not in {companies_csv.name}")
    labels = [DocLabel.from_doc_name(i, n, by_cik[cik]) for i, n, cik in rows]
    backend = None
    if resolved["mode"] == "llm":
        backend = make_backend({"backend": resolved["backend"], "model": resolved["model"]})
    return Scoper(conn, resolved, labels, companies, backend)
