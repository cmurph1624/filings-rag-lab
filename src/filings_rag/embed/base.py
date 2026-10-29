from dataclasses import dataclass
from typing import Literal, Protocol

InputType = Literal["document", "query"]


@dataclass(frozen=True)
class Embeddings:
    vectors: list[list[float]]
    tokens: int


class Embedder(Protocol):
    name: str  # model name, also the price-table key
    dims: int

    def embed(self, texts: list[str], input_type: InputType) -> Embeddings: ...


def to_pgvector(v: list[float]) -> str:
    """pgvector text literal, e.g. '[0.1,0.2]'."""
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"
