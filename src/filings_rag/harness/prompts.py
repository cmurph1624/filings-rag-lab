"""Generation prompts, versioned like pipelines: never edit one in place, add `name@n+1`.

tests/test_harness.py pins a hash of each prompt so an in-place edit fails the build.
"""

import re
from dataclasses import dataclass

from filings_rag.retrieve.base import Hit


@dataclass(frozen=True)
class Prompt:
    system: str
    template: str  # {excerpts}, {question}
    excerpt: str  # {n}, {doc_name}, {page}, {text}, {section} ("" or " · <section>"),
    # {period} ("" or " · <fiscal year/quarter ended date>", from `periods`)

    @property
    def uses_periods(self) -> bool:
        return "{period}" in self.excerpt

    def render(
        self,
        question: str,
        hits: list[Hit],
        periods: dict[str, str] | None = None,
        facts: str = "",
    ) -> str:
        """`periods`: doc_name -> a period phrase such as "fiscal year ended 2023-01-28".
        `facts`: the fact route's Facts section ("" for none); only templates with {facts}
        show it."""
        periods = periods or {}
        excerpts = "\n\n".join(
            self.excerpt.format(
                n=i,
                doc_name=h.doc_name,
                page=h.page,
                text=h.text,
                section=f" · {h.section}" if h.section else "",
                period=f" · {periods[h.doc_name]}" if h.doc_name in periods else "",
            )
            for i, h in enumerate(hits, 1)
        )
        return self.template.format(excerpts=excerpts, question=question, facts=facts)


ANSWER_V1 = Prompt(
    system=(
        "You answer questions about companies' SEC filings using only the numbered excerpts "
        "you are given. If the question needs a calculation, show it briefly. State the answer "
        "with its units and the fiscal period it refers to. If the excerpts do not contain what "
        "the question needs, say you cannot answer from the excerpts; do not guess or use "
        "outside knowledge. Keep the answer under 150 words."
    ),
    template="Excerpts:\n\n{excerpts}\n\nQuestion: {question}",
    excerpt="[{n}] {doc_name}, page {page}\n{text}",
)

# answer@1 plus citations: the answer names the excerpts it used, so the report can measure
# whether they hold the labelled evidence (citation precision).
ANSWER_V2 = Prompt(
    system=(
        "You answer questions about companies' SEC filings using only the numbered excerpts "
        "you are given. If the question needs a calculation, show it briefly. State the answer "
        "with its units and the fiscal period it refers to. Cite the excerpts you used by "
        "number in square brackets, such as [2] or [1][4], right after the statement they "
        "support. If the excerpts do not contain what the question needs, say you cannot "
        "answer from the excerpts; do not guess or use outside knowledge. Keep the answer "
        "under 150 words."
    ),
    template="Excerpts:\n\n{excerpts}\n\nQuestion: {question}",
    excerpt="[{n}] {doc_name}{section}, page {page}\n{text}",
)

# answer@1 plus each filing's period end in the excerpt header, and a note that the year in an
# excerpt's name follows the question's labels. FinanceBench labels some years differently from
# the company (its FY2023 for Ulta is the year Ulta calls fiscal 2022), so the model should match
# the question's period to the excerpt names, not to the company's own year names.
ANSWER_V3 = Prompt(
    system=(
        "You answer questions about companies' SEC filings using only the numbered excerpts "
        "you are given. Each excerpt's header names its filing, and for 10-K and 10-Q filings "
        "gives the period the filing covers. The fiscal year in a filing's name (such as "
        "COMPANY_2023_10K) uses the same labels as the question, even where the company's own "
        "name for that year differs, so match the question's period to the filing names and "
        "use the period-end dates to tell years apart. If the question needs a calculation, "
        "show it briefly. State the answer with its units and the fiscal period it refers to. "
        "If the excerpts do not contain what the question needs, say you cannot answer from the "
        "excerpts; do not guess or use outside knowledge. Keep the answer under 150 words."
    ),
    template="Excerpts:\n\n{excerpts}\n\nQuestion: {question}",
    excerpt="[{n}] {doc_name}{period}, page {page}\n{text}",
)

# answer@3 plus the fact route's Facts section: values computed in code from the filing's XBRL
# data, each with its formula and source facts. The model uses them as given instead of reading
# the numbers out of table excerpts.
ANSWER_V4 = Prompt(
    system=(
        "You answer questions about companies' SEC filings using only the numbered excerpts "
        "and, when given, the Facts section. Facts are computed in code from the filing's XBRL "
        "financial data, each with its formula and the line items it used: use their values "
        "as given for the metrics they cover and cite them as [F1], [F2]. If the question "
        "defines a metric differently from a fact's formula, follow the question and compute "
        "it from the listed line items or the excerpts. A fact marked unavailable was not "
        "found; use the excerpts instead. Each excerpt's header names its filing, and for 10-K "
        "and 10-Q filings gives the period the filing covers. The fiscal year in a filing's "
        "name (such as COMPANY_2023_10K) uses the same labels as the question, even where the "
        "company's own name for that year differs, so match the question's period to the "
        "filing names and use the period-end dates to tell years apart. If the question needs "
        "a calculation, show it briefly. State the answer with its units and the fiscal period "
        "it refers to. If neither the facts nor the excerpts contain what the question needs, "
        "say you cannot answer; do not guess or use outside knowledge. Keep the answer under "
        "150 words."
    ),
    template="{facts}Excerpts:\n\n{excerpts}\n\nQuestion: {question}",
    excerpt="[{n}] {doc_name}{period}, page {page}\n{text}",
)

PROMPTS: dict[str, Prompt] = {
    "answer@1": ANSWER_V1,
    "answer@2": ANSWER_V2,
    "answer@3": ANSWER_V3,
    "answer@4": ANSWER_V4,
}

CITATION = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def citations(answer: str, n_excerpts: int) -> list[int]:
    """Excerpt numbers an answer cites ("[2]", "[1][4]", "[1, 3]"), in order, de-duplicated."""
    seen: list[int] = []
    for m in CITATION.finditer(answer):
        for part in m[1].split(","):
            k = int(part)
            if 1 <= k <= n_excerpts and k not in seen:
                seen.append(k)
    return seen


def get_prompt(ref: str) -> Prompt:
    if ref not in PROMPTS:
        raise ValueError(f"unknown prompt {ref!r}; known: {sorted(PROMPTS)}")
    return PROMPTS[ref]


@dataclass(frozen=True)
class JudgePrompt:
    system: str
    template: str  # {question}, {gold}, {justification}, {answer}

    def render(self, question: str, gold: str, justification: str | None, answer: str) -> str:
        return self.template.format(
            question=question, gold=gold, justification=justification or "(none)", answer=answer
        )


JUDGE_V1 = JudgePrompt(
    system=(
        "You grade a candidate answer to a question about a company's SEC filings against the "
        "gold answer. Verdicts:\n"
        "- correct: the candidate reaches the same conclusion as the gold answer. Numbers must "
        "match after rounding to the gold answer's precision (allow about 1% for computed "
        "ratios) and refer to the same period and units. Extra detail is fine if it does not "
        "contradict the gold answer.\n"
        "- incorrect: a different number, period, or conclusion; an answer that contradicts the "
        "gold answer; or one that hedges between several answers.\n"
        "- refused: the candidate declines, or says the information is not available, without "
        "committing to an answer.\n"
        "Judge only against the gold answer and its reasoning, not your own knowledge. Reply "
        'with only a JSON object: {"verdict": "correct" | "incorrect" | "refused", '
        '"rationale": "<one or two sentences>"}'
    ),
    template=(
        "Question: {question}\n\nGold answer: {gold}\n\nGold reasoning: {justification}\n\n"
        "Candidate answer:\n{answer}"
    ),
)

JUDGE_PROMPTS: dict[str, JudgePrompt] = {"judge@1": JUDGE_V1}


def get_judge_prompt(ref: str) -> JudgePrompt:
    if ref not in JUDGE_PROMPTS:
        raise ValueError(f"unknown judge prompt {ref!r}; known: {sorted(JUDGE_PROMPTS)}")
    return JUDGE_PROMPTS[ref]


@dataclass(frozen=True)
class FaithfulPrompt:
    """Checks an answer against the context the generator was given, not against the gold."""

    system: str
    template: str  # {context}, {answer}

    def render(self, context: str, answer: str) -> str:
        return self.template.format(context=context, answer=answer)


FAITHFUL_V1 = FaithfulPrompt(
    system=(
        "You check whether an answer about a company's SEC filings is supported by the context "
        "its writer was given: numbered excerpts and, sometimes, a Facts section of values "
        "computed from XBRL data. List the answer's checkable claims: each figure (amount, "
        "ratio, percentage, count, date or period) and each factual statement about the "
        "company that the answer asserts. Skip hedges, advice, definitions and statements "
        "that the context lacks something. For each claim, give one support label:\n"
        "- supported: the context states it, or it follows by arithmetic from figures the "
        "context states (allow rounding to the answer's precision).\n"
        "- unsupported: the context neither states it nor lets it be computed. Outside "
        "knowledge that happens to be true is still unsupported.\n"
        "- contradicted: the context gives a different value or says the opposite, for the "
        "same item and period.\n"
        "Judge only against the context, never your own knowledge. A refusal with no claims "
        "gets an empty list. Reply with only a JSON object: "
        '{"claims": [{"claim": "<short quote or paraphrase>", "support": "supported" | '
        '"unsupported" | "contradicted", "source": "<[n] or [Fn] where it is, or none>"}]}'
    ),
    template="Context the writer was given:\n\n{context}\n\nAnswer to check:\n{answer}",
)

FAITHFUL_PROMPTS: dict[str, FaithfulPrompt] = {"faithful@1": FAITHFUL_V1}


def get_faithful_prompt(ref: str) -> FaithfulPrompt:
    if ref not in FAITHFUL_PROMPTS:
        raise ValueError(f"unknown faithfulness prompt {ref!r}; known: {sorted(FAITHFUL_PROMPTS)}")
    return FAITHFUL_PROMPTS[ref]


@dataclass(frozen=True)
class SynthPrompt:
    """Writes one retrieval question from a filing excerpt (`frl bench synth-prose`)."""

    system: str
    template: str  # {company}, {form}, {year}, {section}, {excerpt}

    def render(self, company: str, form: str, year: int, section: str, excerpt: str) -> str:
        return self.template.format(
            company=company, form=form, year=year, section=section, excerpt=excerpt
        )


SYNTH_V1 = SynthPrompt(
    system=(
        "You write test questions for a system that searches companies' SEC filings. Given "
        "one excerpt from a filing, write a single question that a financial analyst could "
        "ask without having seen the excerpt, and whose answer is stated in the excerpt. "
        "Requirements:\n"
        "- Name the company and the fiscal year in the form FY2021 (use exactly the year "
        "you are given, and no other year).\n"
        "- Stand alone: never refer to an excerpt, passage, text, section or document.\n"
        "- Ask about the substance (what, why, how, which, how much), not the wording. Do not "
        "copy phrases from the excerpt.\n"
        "- Prefer a question that this excerpt answers better than the rest of the filing "
        "would; avoid generic questions that any 10-K could answer.\n"
        "- Write the answer in one to three sentences, using only the excerpt.\n"
        "If the excerpt has no substantive content to ask about (boilerplate, a list of "
        'names, a table of numbers), reply {"skip": "<reason>"}. Otherwise reply with only a '
        'JSON object: {"question": "<question>", "answer": "<answer>"}'
    ),
    template=(
        "Company: {company}\nFiling: {form}, fiscal year FY{year}\nSection: {section}\n\n"
        "Excerpt:\n{excerpt}"
    ),
)

SYNTH_PROMPTS: dict[str, SynthPrompt] = {"synth-prose@1": SYNTH_V1}


def get_synth_prompt(ref: str) -> SynthPrompt:
    if ref not in SYNTH_PROMPTS:
        raise ValueError(
            f"unknown synthetic-question prompt {ref!r}; known: {sorted(SYNTH_PROMPTS)}"
        )
    return SYNTH_PROMPTS[ref]
