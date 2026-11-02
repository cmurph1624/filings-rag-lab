import pytest

from filings_rag.eval.faithful import ClaimsError, judge_key, parse_claims
from filings_rag.harness.config import from_dict


def test_parse_claims_normalises_and_tolerates_fences() -> None:
    text = (
        'Here:\n```json\n{"claims": [{"claim": "Revenue $5B", "support": "Supported",'
        ' "source": "[2]"}, {"claim": "x", "support": "contradicted", "source": null}]}\n```'
    )
    assert parse_claims(text) == [
        {"claim": "Revenue $5B", "support": "supported", "source": "[2]"},
        {"claim": "x", "support": "contradicted", "source": "none"},
    ]
    assert parse_claims('{"claims": []}') == []


@pytest.mark.parametrize(
    "text",
    ["no json", '{"claims": "x"}', '{"claims": [{"support": "maybe"}]}', '{"claims": [1]}', "{,}"],
)
def test_parse_claims_rejects_bad_replies(text: str) -> None:
    with pytest.raises(ClaimsError):
        parse_claims(text)


def test_judge_key_swaps_the_prompt_only() -> None:
    cfg = from_dict(
        {
            "name": "x",
            "pipeline": "structured@1",
            "retriever": {"kind": "vector"},
            "generation": {
                "backend": "claude-cli",
                "model": "claude-sonnet-5-5",
                "prompt": "answer@4",
            },
            "judge": {
                "backend": "claude-cli",
                "model": "claude-opus-5-5",
                "effort": "high",
                "prompt": "judge@1",
            },
        }
    )
    assert judge_key(cfg) == "claude-cli:claude-opus-5-5:high:faithful@1"
