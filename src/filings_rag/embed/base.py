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
