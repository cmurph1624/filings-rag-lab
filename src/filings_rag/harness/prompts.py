"""Generation prompts, versioned like pipelines: never edit one in place, add `name@n+1`.

tests/test_harness.py pins a hash of each prompt so an in-place edit fails the build.
"""

from dataclasses import dataclass

from filings_rag.retrieve.base import Hit


@dataclass(frozen=True)
class Prompt:
    system: str
    template: str  # {excerpts}, {question}
    excerpt: str  # {n}, {doc_name}, {page}, {text}

    def render(self, question: str, hits: list[Hit]) -> str:
        excerpts = "\n\n".join(
            self.excerpt.format(n=i, doc_name=h.doc_name, page=h.page, text=h.text)
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

PROMPTS: dict[str, Prompt] = {"answer@1": ANSWER_V1}


def get_prompt(ref: str) -> Prompt:
    if ref not in PROMPTS:
        raise ValueError(f"unknown prompt {ref!r}; known: {sorted(PROMPTS)}")
    return PROMPTS[ref]
