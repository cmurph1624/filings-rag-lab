"""Claude Code in print mode (`claude -p`) on the personal Max subscription.

For local benchmark runs only; the public deployment uses the API backend. Every call runs
with no tools, settings, MCP servers or slash commands, from an empty directory, with our own
system prompt, so token counts reflect the prompt rather than Claude Code's context.

Auth: the child process gets no ANTHROPIC_* variables. If ANTHROPIC_API_KEY (or a token or
base URL) is set, Claude Code uses it ahead of the subscription login and "subscription" runs
bill the API. CLAUDE_* session variables are dropped too (CLAUDE_EFFORT overrides --effort),
except the two that locate the subscription login itself.
"""

import json
import os
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from typing import Any

from filings_rag.backends.base import BackendError, Result
from filings_rag.pricing import Usage

Runner = Callable[..., subprocess.CompletedProcess[str]]
STRIP_PREFIXES = ("ANTHROPIC_", "CLAUDE_")
KEEP = frozenset({"CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN"})  # subscription login


def child_env(env: Mapping[str, str]) -> dict[str, str]:
    return {k: v for k, v in env.items() if k in KEEP or not k.startswith(STRIP_PREFIXES)}


class ClaudeCliBackend:
    name = "claude-cli"

    def __init__(
        self,
        model: str,
        *,
        effort: str | None = None,
        timeout_s: float = 600,
        executable: str = "claude",
        runner: Runner = subprocess.run,
    ) -> None:
        self.model = model
        self.effort = effort
        self.timeout_s = timeout_s
        self.executable = executable
        self._run = runner

    def argv(self, system: str) -> list[str]:
        args = [
            self.executable, "-p",
            "--output-format", "json",
            "--model", self.model,
            "--system-prompt", system,
            "--tools", "",
            "--setting-sources", "",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--no-session-persistence",
        ]  # fmt: skip
        if self.effort:
            args += ["--effort", self.effort]
        return args

    def generate(self, system: str, prompt: str) -> Result:
        start = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="frl-cli-") as cwd:
            try:
                proc = self._run(
                    self.argv(system),
                    input=prompt,  # stdin: argv prompts get truncated (see claude-cron-jobs)
                    capture_output=True,
                    text=True,
                    cwd=cwd,
                    env=child_env(os.environ),
                    timeout=self.timeout_s,
                )
            except subprocess.TimeoutExpired as e:
                raise BackendError(f"claude -p timed out after {self.timeout_s}s") from e
        latency = time.monotonic() - start
        return parse_output(proc.stdout, proc.returncode, proc.stderr, latency, self.model)


def parse_output(stdout: str, returncode: int, stderr: str, latency: float, model: str) -> Result:
    """Parse the final JSON object `claude -p --output-format json` prints."""
    line = next((ln for ln in reversed(stdout.splitlines()) if ln.lstrip().startswith("{")), "")
    try:
        data: dict[str, Any] = json.loads(line)
    except json.JSONDecodeError as e:
        raise BackendError(f"claude -p rc={returncode}, no JSON output: {stderr[-500:]}") from e
    if data.get("is_error") or returncode != 0:
        raise BackendError(
            f"claude -p failed: {data.get('subtype')} {data.get('api_error_status')} "
            f"{str(data.get('result'))[:300]}"
        )
    u = data.get("usage", {})
    writes = int(u.get("cache_creation_input_tokens", 0))
    writes_1h = int((u.get("cache_creation") or {}).get("ephemeral_1h_input_tokens", 0))
    models = list(data.get("modelUsage") or {}) or [model]
    if len(models) > 1:
        # Aggregate usage would mix models under one label and one price.
        raise BackendError(f"claude -p used several models in one call: {models}")
    served = models[0]
    return Result(
        text=str(data.get("result", "")),
        usage=Usage(
            input_tokens=int(u.get("input_tokens", 0)),
            output_tokens=int(u.get("output_tokens", 0)),
            cache_read_tokens=int(u.get("cache_read_input_tokens", 0)),
            cache_write_tokens=writes - writes_1h,
            cache_write_1h_tokens=writes_1h,
        ),
        latency_s=latency,
        model=served,
        stop_reason=str(data.get("stop_reason", "")),
        reported_cost_usd=data.get("total_cost_usd"),
    )
