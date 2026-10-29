import hashlib
from pathlib import Path

import pytest

from filings_rag.harness.config import from_dict, load
from filings_rag.harness.prompts import JUDGE_PROMPTS, PROMPTS, get_prompt
from filings_rag.retrieve.base import Hit
from filings_rag.retrieve.factory import make_retriever

ROOT = Path(__file__).resolve().parents[1]
# Prompts are versioned: editing one in place changes results under the same name.
PINNED = {"answer@1": "6def313c45d7a981"}
PINNED_JUDGE = {"judge@1": "d22124b2220d1927"}


def base() -> dict:  # type: ignore[type-arg]
    return {
        "name": "t",
        "pipeline": "naive-fixed@1",
        "retriever": {"kind": "vector", "k": 10},
        "generation": {"backend": "claude-cli", "model": "claude-sonnet-5-5", "prompt": "answer@1"},
        "judge": {"backend": "claude-cli", "model": "claude-opus-5-5", "prompt": "judge@1"},
    }


def test_shipped_config_loads() -> None:
    cfg = load(ROOT / "configs" / "p0-naive.toml")
    assert cfg.pipeline == "naive-fixed@1" and cfg.questions == "financebench-open"
    assert cfg.backend_spec == {"backend": "claude-cli", "model": "claude-sonnet-5-5"}
    assert cfg.execution["concurrency"] == 2


def test_hash_ignores_name_execution_and_key_order() -> None:
    a = from_dict(base())
    b = from_dict(
        {**dict(reversed(base().items())), "name": "other", "execution": {"concurrency": 3}}
    )
    assert a.hash == b.hash
    c = base()
    c["retriever"]["k"] = 5
    assert from_dict(c).hash != a.hash
    d = base()
    d["judge"]["model"] = "claude-sonnet-5-5"
    assert from_dict(d).hash != a.hash  # the judge is pinned per run


def test_default_question_set_is_hashed_explicitly() -> None:
    assert from_dict(base()).hash == from_dict({**base(), "questions": "financebench-open"}).hash


def test_config_rejects_typos() -> None:
    with pytest.raises(ValueError, match="unknown keys"):
        from_dict({**base(), "retreiver": {}})
    with pytest.raises(ValueError, match="missing"):
        from_dict({k: v for k, v in base().items() if k != "retriever"})
    with pytest.raises(ValueError, match="prompt"):
        from_dict({**base(), "generation": {"backend": "claude-cli", "model": "m"}})
    with pytest.raises(ValueError, match="execution"):
        from_dict({**base(), "execution": {"concurency": 2}})


def test_retriever_factory_rejects_unknown_kind_and_options() -> None:
    with pytest.raises(ValueError, match="kind"):
        make_retriever(None, {"kind": "bm25"}, "t", None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="options"):
        make_retriever(None, {"kind": "vector", "top_k": 5}, "t", None)  # type: ignore[arg-type]


@pytest.mark.parametrize("ref", sorted(PROMPTS))
def test_prompts_are_not_edited_in_place(ref: str) -> None:
    p = PROMPTS[ref]
    digest = hashlib.sha256(f"{p.system}\0{p.template}\0{p.excerpt}".encode()).hexdigest()[:16]
    assert PINNED.get(ref) == digest, f"{ref} changed: add a new version instead"


@pytest.mark.parametrize("ref", sorted(JUDGE_PROMPTS))
def test_judge_prompts_are_not_edited_in_place(ref: str) -> None:
    p = JUDGE_PROMPTS[ref]
    digest = hashlib.sha256(f"{p.system}\0{p.template}".encode()).hexdigest()[:16]
    assert PINNED_JUDGE.get(ref) == digest, f"{ref} changed: add a new version instead"


def test_judge_key_names_backend_model_effort_and_prompt() -> None:
    assert from_dict(base()).judge_key == "claude-cli:claude-opus-5-5:judge@1"
    b = base()
    b["judge"]["effort"] = "high"
    assert from_dict(b).judge_key == "claude-cli:claude-opus-5-5:high:judge@1"


def test_prompt_renders_numbered_excerpts() -> None:
    hits = [Hit(1, "3M_2018_10K", 47, 0.8, "Revenue | 32,765"), Hit(2, "3M_2018_10K", 48, 0.7, "x")]
    out = get_prompt("answer@1").render("What was 3M's FY2018 revenue?", hits)
    assert "[1] 3M_2018_10K, page 47\nRevenue | 32,765" in out
    assert "[2] 3M_2018_10K, page 48" in out
    assert out.endswith("Question: What was 3M's FY2018 revenue?")
    with pytest.raises(ValueError):
        get_prompt("answer@99")


def test_hash_covers_retriever_defaults_not_spelling() -> None:
    implicit = from_dict(base())
    explicit = base()
    explicit["retriever"] = {"kind": "vector", "k": 10, "search": "exact", "ef_search": 400}
    assert from_dict(explicit).hash == implicit.hash  # ef_search is unused by exact search
    hnsw = base()
    hnsw["retriever"] = {"kind": "vector", "k": 10, "search": "hnsw"}
    assert from_dict(hnsw).hash != implicit.hash
    with pytest.raises(ValueError, match="options"):
        from_dict({**base(), "retriever": {"kind": "vector", "topk": 3}})  # caught at load


def test_p0_config_hash_is_unchanged() -> None:
    # Run 60 (the P0 baseline) was recorded with this hash; refactors must not move it.
    assert load(ROOT / "configs" / "p0-naive.toml").hash == "6cc9a6708ce5ab47"


def test_retrieval_only_config() -> None:
    raw = {k: v for k, v in base().items() if k not in ("generation", "judge")}
    cfg = from_dict(raw)
    assert cfg.generation is None and cfg.judge is None
    assert cfg.backend_spec is None and cfg.judge_key is None
    assert cfg.hash != from_dict(base()).hash
    with pytest.raises(ValueError, match="go together"):
        from_dict({**raw, "judge": base()["judge"]})
    with pytest.raises(ValueError, match="go together"):
        from_dict({**raw, "generation": base()["generation"]})


def test_default_corpus_keeps_p0_hashes_and_others_are_hashed() -> None:
    implicit = from_dict(base())
    assert implicit.corpus == "fb-gold@1"
    assert from_dict({**base(), "corpus": "fb-gold@1"}).hash == implicit.hash
    distractors = from_dict({**base(), "corpus": "fb-distractors@1"})
    assert distractors.corpus == "fb-distractors@1" and distractors.hash != implicit.hash
    with pytest.raises(ValueError, match="unknown corpus"):
        from_dict({**base(), "corpus": "fb-gold"})  # versions are explicit
