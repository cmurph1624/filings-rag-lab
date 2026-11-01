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

Period options (P2; each is left out of the resolved spec at its default, so configs that don't
set it keep their hashes and their behaviour):
- `forecast_lookback = n`: a forward-looking question ("expected", "forecast", "guidance",
  "outlook"...) also matches annual filings up to n years before each year it names, because a
  forecast for FY2023 is made in the FY2022 filing.
- `no_year = "latest"`: a question that names no period gets the company's filings from its
  latest labelled year, instead of all of them (`"company"`, the default).
- `fiscal_labels = "financebench"`: distractor filings (not FinanceBench's own documents) are
  relabelled with the company's `label_offset` from companies.csv. FinanceBench labels Ulta's
  years one ahead of the company's own (its FY2023 is Ulta's fiscal 2022), so Ulta's distractors
  get +1 to match the labels the questions use. FinanceBench's documents keep their labels.
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
FORWARD = re.compile(
    r"\b(?:expect(?:s|ed|ing|ation|ations)?|forecast\w*|guidance|outlook|anticipat\w*"
    r"|projected|projections?)\b",
    re.IGNORECASE,
)
HALF = re.compile(r"\bH([12])\s*(?:of\s+)?(?:FY\s*)?" + _Y4, re.IGNORECASE)
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4}


def is_forward_looking(text: str) -> bool:
    return FORWARD.search(text) is not None


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
    # Any 19xx/20xx number counts as a year, so a figure like "2,024 units" written "2024"
    # over-matches. That is accepted: a spurious year widens the scope (costs precision), it
    # never drops the filing the question is about.
    return sorted(years), sorted(quarters)


@dataclass(frozen=True)
class Company:
    name: str
    ticker: str
    aliases: tuple[str, ...]
    label_offset: int = 0  # FinanceBench's fiscal-year label minus the company's own


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
        """Lenient: years that aren't integers and quarters that aren't [year, 1-4] are dropped."""
        years: set[int] = set()
        raw_years, raw_quarters = d.get("years"), d.get("quarters")
        for y in raw_years if isinstance(raw_years, list) else []:
            try:
                years.add(int(y))
            except (TypeError, ValueError):
                continue
        quarters: set[tuple[int, int]] = set()
        for pair in raw_quarters if isinstance(raw_quarters, list) else []:
            try:
                y, q = (int(x) for x in pair)
            except (TypeError, ValueError):
                continue
            if 1 <= q <= 4:
                quarters.add((y, q))
        company = d.get("company")
        return cls(company if isinstance(company, str) else None, sorted(years), sorted(quarters))


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
    def from_doc_name(cls, doc_id: int, doc_name: str, company: str, offset: int = 0) -> "DocLabel":
        ref = parse_doc_name(doc_name)
        return cls(doc_id, doc_name, company, ref.fiscal_year + offset, ref.fiscal_period)

    def matches(self, m: Mentions, lookahead: int, lookback: int = 0) -> bool:
        if self.period in ANNUAL:
            return any(y - lookback <= self.year <= y + lookahead for y in m.years)
        return (self.year, int(self.period[1])) in m.quarters


def period_groups(
    docs: list[DocLabel], m: Mentions, lookahead: int, lookback: int = 0
) -> list[list[DocLabel]]:
    """`docs` (a scope's filings) split by the periods `m` names: one group per year (annual
    filings, with the same lookahead/lookback window as `select`) and per quarter (quarterly
    filings). Empty and repeated groups are dropped, so a single-period question gets one."""
    windows = [
        [d for d in docs if d.period in ANNUAL and y - lookback <= d.year <= y + lookahead]
        for y in m.years
    ]
    quarters = [
        [d for d in docs if d.period not in ANNUAL and (d.year, int(d.period[1])) == yq]
        for yq in m.quarters
    ]
    out: list[list[DocLabel]] = []
    for g in windows + quarters:
        if g and g not in out:
            out.append(g)
    return out


def select(
    labels: list[DocLabel],
    m: Mentions,
    lookahead: int,
    *,
    lookback: int = 0,
    no_year: str = "company",
) -> tuple[list[DocLabel], str]:
    """(filings in scope, level): level is "filings", "latest" (no period named, latest year),
    "company" (fallback) or "corpus"."""
    own = [d for d in labels if d.company == m.company]
    if not own:
        return labels, "corpus"
    if no_year == "latest" and not m.years and not m.quarters:
        top = max(d.year for d in own)
        return [d for d in own if d.year == top], "latest"
    hit = [d for d in own if d.matches(m, lookahead, lookback)]
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
# P2 period options for rules and llm, with the value that means "off". They are only in the
# resolved spec when set to something else, so older configs keep their hashes.
PERIOD_OPTIONS: dict[str, Any] = {
    "forecast_lookback": 0,
    "no_year": "company",
    "fiscal_labels": "doc_name",
}
PERIOD_CHOICES: dict[str, tuple[str, ...]] = {
    "no_year": ("company", "latest"),
    "fiscal_labels": ("doc_name", "financebench"),
}


def resolve_scope(spec: dict[str, Any] | None) -> dict[str, Any] | None:
    """The [retriever.scope] table with defaults filled in; None for no scoping."""
    if not spec:
        return None
    mode = spec.get("mode", "none")
    if mode not in SCOPE_DEFAULTS:
        raise ValueError(f"unknown scope mode {mode!r}; known: {list(SCOPE_DEFAULTS)}")
    opts = {k: v for k, v in spec.items() if k != "mode"}
    allowed = SCOPE_DEFAULTS[mode].keys() | (
        PERIOD_OPTIONS.keys() if mode in ("rules", "llm") else set()
    )
    unknown = opts.keys() - allowed
    if unknown:
        raise ValueError(f"unknown {mode} scope options: {sorted(unknown)}")
    if mode == "none":
        return None
    for k, choices in PERIOD_CHOICES.items():
        if k in opts and opts[k] not in choices:
            raise ValueError(f"scope {k} must be one of {list(choices)}, not {opts[k]!r}")
    opts = {k: v for k, v in opts.items() if PERIOD_OPTIONS.get(k, object()) != v}
    resolved = {"mode": mode, **(SCOPE_DEFAULTS[mode] | opts)}
    if resolved.get("lookahead", 0) < 0 or resolved.get("forecast_lookback", 0) < 0:
        raise ValueError("scope lookahead and forecast_lookback must be 0 or more")
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
    if not isinstance(data, dict):
        raise ValueError(f"scope reply is not a JSON object: {m[0][:200]!r}")
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
    # `documents` split by mentioned period (one group per year or quarter that has filings in
    # scope), for decomposed retrieval. Empty unless the scope narrowed to filings.
    groups: list[list[int]] = field(default_factory=list)


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
        groups: list[list[DocLabel]] = []
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
            forward = bool(self.spec.get("forecast_lookback")) and is_forward_looking(question)
            lookback = self.spec.get("forecast_lookback", 0) if forward else 0
            docs, level = select(
                self._labels,
                m,
                self.spec.get("lookahead", 0),
                lookback=lookback,
                no_year=self.spec.get("no_year", "company"),
            )
            if level == "filings":
                groups = period_groups(docs, m, self.spec.get("lookahead", 0), lookback)
            mentions = m.to_json()
            if forward:
                mentions["forward"] = True
        record = {"mode": self.mode, **mentions, "level": level}
        if aux and aux.get("parse_error"):
            record["parse_error"] = aux["parse_error"]
        record["docs"] = [d.doc_name for d in docs] if level != "corpus" else []
        return Scope([d.id for d in docs], record, aux, [[d.id for d in g] for g in groups])

    def _llm(self, question_id: str, question: str) -> tuple[Mentions, dict[str, Any]]:
        """Mentions from the scope model, cached. A reply that won't parse is treated like rules
        that found nothing (no company: the whole corpus), and is cached with its cost and the
        parse error, so the spend is recorded and a resume doesn't pay for it again."""
        row = self._conn.execute(
            "SELECT mentions, model, usage, cost_usd FROM query_scopes"
            " WHERE question_id = %s AND scope_key = %s",
            (question_id, self.key),
        ).fetchone()
        self._conn.commit()
        if row is not None:
            mentions, model, usage, cost = row
            hit = {"model": model, "usage": usage, "cost_usd": cost, "cached": True}
            if mentions.get("parse_error"):
                hit["parse_error"] = mentions["parse_error"]
            return Mentions.from_json(mentions), hit
        assert self._backend is not None
        names = [c.name for c in self._companies]
        result = self._backend.generate(self._prompt.system, self._prompt.render(question, names))
        cost = model_cost(result.model, result.usage)
        usage = asdict(result.usage)
        aux: dict[str, Any] = {"model": result.model, "usage": usage, "cost_usd": cost}
        try:
            m = parse_llm_mentions(result.text, names)
            stored = m.to_json()
        except (ValueError, TypeError, AttributeError) as e:  # any reply shape we can't read
            m = Mentions(None)
            aux["parse_error"] = f"{e}"[:300]
            stored = {**m.to_json(), "parse_error": aux["parse_error"]}
        self._conn.execute(
            "INSERT INTO query_scopes (question_id, scope_key, mentions, model, usage, cost_usd)"
            " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (question_id, self.key, Jsonb(stored), result.model, Jsonb(usage), cost),
        )
        self._conn.commit()
        return m, {**aux, "cached": False}


def read_companies(path: Path) -> tuple[list[Company], dict[int, str]]:
    """(companies with aliases, cik -> company) from corpus/companies.csv."""
    with path.open() as fh:
        rows = list(csv.DictReader(fh))
    companies = [
        Company(
            r["company"],
            r["ticker"],
            tuple(a for a in r.get("aliases", "").split(";") if a),
            int(r.get("label_offset") or 0),
        )
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
    offsets: dict[str, int] = {}
    benchmark_docs: set[str] = set()
    if resolved.get("fiscal_labels") == "financebench":
        offsets = {c.name: c.label_offset for c in companies}
        benchmark_docs = {r[0] for r in conn.execute("SELECT doc_name FROM benchmark_docs")}
        conn.commit()
    labels = [
        DocLabel.from_doc_name(
            i, n, by_cik[cik], 0 if n in benchmark_docs else offsets.get(by_cik[cik], 0)
        )
        for i, n, cik in rows
    ]
    backend = None
    if resolved["mode"] == "llm":
        backend = make_backend({"backend": resolved["backend"], "model": resolved["model"]})
    return Scoper(conn, resolved, labels, companies, backend)
