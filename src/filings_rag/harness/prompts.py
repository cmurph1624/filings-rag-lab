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
    excerpt: str  # {n}, {doc_name}, {page}, {text}, {section} ("" or " · <section>")

    def render(self, question: str, hits: list[Hit]) -> str:
        excerpts = "\n\n".join(
            self.excerpt.format(
                n=i,
                doc_name=h.doc_name,
                page=h.page,
                text=h.text,
                section=f" · {h.section}" if h.section else "",
            )
            for i, h in enumerate(hits, 1)
        )
        return self.template.format(excerpts=excerpts, question=question)


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

PROMPTS: dict[str, Prompt] = {"answer@1": ANSWER_V1, "answer@2": ANSWER_V2}

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
