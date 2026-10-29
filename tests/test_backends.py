import json
import subprocess
from types import SimpleNamespace
from typing import Any

import pytest

from filings_rag.backends.anthropic_api import AnthropicApiBackend
from filings_rag.backends.base import BackendError
from filings_rag.backends.claude_cli import ClaudeCliBackend, child_env, parse_output
from filings_rag.backends.factory import make_backend

# Trimmed from a real `claude -p --output-format json` result (CLI 2.1.312).
CLI_OK = {
    "type": "result", "subtype": "success", "is_error": False, "stop_reason": "end_turn",
    "result": "391", "total_cost_usd": 0.00089,
    "usage": {"input_tokens": 430, "cache_creation_input_tokens": 5,
              "cache_read_input_tokens": 7, "output_tokens": 3,
              "cache_creation": {"ephemeral_1h_input_tokens": 3, "ephemeral_5m_input_tokens": 2}},
    "modelUsage": {"claude-sonnet-5-5": {"inputTokens": 430, "outputTokens": 3}},
}  # fmt: skip


def test_child_env_strips_api_keys_and_session_vars() -> None:
    env = {
        "PATH": "/bin", "HOME": "/h", "ANTHROPIC_API_KEY": "sk", "ANTHROPIC_AUTH_TOKEN": "t",
        "ANTHROPIC_BASE_URL": "u", "CLAUDE_EFFORT": "max", "CLAUDE_CODE_SESSION_ID": "x",
        "CLAUDE_CONFIG_DIR": "/cfg", "CLAUDE_CODE_OAUTH_TOKEN": "oauth",
    }  # fmt: skip
    assert child_env(env) == {
        "PATH": "/bin", "HOME": "/h",
        "CLAUDE_CONFIG_DIR": "/cfg", "CLAUDE_CODE_OAUTH_TOKEN": "oauth",
    }  # fmt: skip


def test_cli_runs_isolated_with_prompt_on_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    seen: dict[str, Any] = {}

    def runner(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        seen.update(argv=argv, **kw)
        return subprocess.CompletedProcess(argv, 0, json.dumps(CLI_OK), "")

    r = ClaudeCliBackend("claude-sonnet-5-5", effort="low", runner=runner).generate("SYS", "Q?")
    argv = seen["argv"]
    assert argv[:2] == ["claude", "-p"]
    assert argv[argv.index("--tools") + 1] == "" and "--strict-mcp-config" in argv
    assert argv[argv.index("--system-prompt") + 1] == "SYS"
    assert argv[argv.index("--effort") + 1] == "low"
    assert "Q?" not in argv and seen["input"] == "Q?"
    assert "ANTHROPIC_API_KEY" not in seen["env"]
    assert r.text == "391" and r.model == "claude-sonnet-5-5"
    assert (r.usage.input_tokens, r.usage.output_tokens) == (430, 3)
    assert (r.usage.cache_write_tokens, r.usage.cache_write_1h_tokens) == (2, 3)
    assert r.usage.cache_read_tokens == 7
    assert r.reported_cost_usd == 0.00089 and not r.refused


def test_cli_parse_errors() -> None:
    with pytest.raises(BackendError):
        parse_output("not json", 1, "boom", 0.1, "m")
    err = {**CLI_OK, "is_error": True, "subtype": "error_during_execution"}
    with pytest.raises(BackendError):
        parse_output(json.dumps(err), 1, "", 0.1, "m")
    two = {**CLI_OK, "modelUsage": {"claude-sonnet-5-5": {}, "claude-haiku-5-5": {}}}
    with pytest.raises(BackendError, match="several models"):
        parse_output(json.dumps(two), 0, "", 0.1, "claude-sonnet-5-5")
    # warnings printed before the JSON line are tolerated
    assert parse_output("warning line\n" + json.dumps(CLI_OK), 0, "", 0.1, "m").text == "391"


def test_cli_timeout_becomes_backend_error() -> None:
    def runner(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(argv, 1)

    with pytest.raises(BackendError):
        ClaudeCliBackend("m", runner=runner).generate("s", "p")


class FakeMessages:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    def create(self, **kw: Any) -> Any:
        self.kwargs = kw
        return SimpleNamespace(
            content=[
                SimpleNamespace(type="thinking", thinking=""),
                SimpleNamespace(type="text", text="ans"),
            ],  # fmt: skip
            usage=SimpleNamespace(
                input_tokens=10,
                output_tokens=4,
                cache_read_input_tokens=None,
                cache_creation_input_tokens=2,
            ),  # fmt: skip
            model="claude-opus-5-5",
            stop_reason="end_turn",
        )


def test_api_backend_maps_request_and_usage() -> None:
    msgs = FakeMessages()
    b = AnthropicApiBackend("claude-opus-5-5", effort="high", client=SimpleNamespace(messages=msgs))
    r = b.generate("SYS", "Q?")
    assert msgs.kwargs["system"] == "SYS"
    assert msgs.kwargs["messages"] == [{"role": "user", "content": "Q?"}]
    assert msgs.kwargs["output_config"] == {"effort": "high"}
    assert "fallbacks" not in msgs.kwargs
    assert r.text == "ans" and r.usage.cache_read_tokens == 0 and r.usage.cache_write_tokens == 2


def test_factory() -> None:
    b = make_backend({"backend": "claude-cli", "model": "claude-sonnet-5-5", "effort": "low"})
    assert isinstance(b, ClaudeCliBackend) and b.effort == "low"
    with pytest.raises(ValueError):
        make_backend({"backend": "nope", "model": "x"})


def test_price_table_matches_cli_reported_cost() -> None:
    """Raw `claude -p` results from 2026-10-29: our list-price cost == the CLI's reported cost."""
    from filings_rag.pricing import model_cost

    samples = [
        # (model, input, output, cache reads, 1h cache writes, CLI total_cost_usd)
        ("claude-sonnet-5-5", 430, 3, 0, 0, 0.00089),
        ("claude-opus-5-5", 2, 3, 0, 4733, 0.037932),
        ("claude-opus-5-5", 2, 3, 4429, 305, 0.0033938),  # cache-read rate pinned here
    ]
    for model, inp, out, reads, w1h, reported in samples:
        raw = {**CLI_OK, "total_cost_usd": reported,
               "usage": {"input_tokens": inp, "output_tokens": out,
                         "cache_read_input_tokens": reads,
                         "cache_creation_input_tokens": w1h,
                         "cache_creation": {"ephemeral_1h_input_tokens": w1h}},
               "modelUsage": {model: {}}}  # fmt: skip
        r = parse_output(json.dumps(raw), 0, "", 0.1, model)
        assert model_cost(r.model, r.usage) == pytest.approx(reported, abs=1e-7)
