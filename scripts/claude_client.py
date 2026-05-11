"""
claude_client.py
LLM caller for the job-applier pipeline.

Uses claude CLI exclusively for all AI tasks.
No API keys required — runs inside a Claude Code session.
"""

import json
import logging
import os
import subprocess
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-6"


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def _clean_env() -> dict:
    """Strip CLAUDECODE so subprocesses can call the claude CLI freely."""
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    return env


def _which(cmd: str) -> Optional[str]:
    import shutil
    return shutil.which(cmd) or shutil.which(f"{cmd}.cmd")


# ─────────────────────────────────────────────────────────
# Provider
# ─────────────────────────────────────────────────────────

def _run_cli(args: list[str], prompt: str, stdin_input: str | None = None) -> Optional[str]:
    """Run a CLI command and return stdout on success, None on failure."""
    env = _clean_env()
    try:
        result = subprocess.run(
            args,
            input=stdin_input,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            env=env,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        if result.returncode == 0 and not result.stdout.strip():
            # CLI exited cleanly but produced no output — check stderr
            stderr_msg = (result.stderr or "").strip()[:300]
            log.warning(f"{args[0]} returned empty output (exit 0). stderr: {stderr_msg}")
        if result.returncode != 0:
            stdout_msg = (result.stdout or "").strip()[:200]
            stderr_msg = (result.stderr or "").strip()[:200]
            combined = stdout_msg or stderr_msg
            if "limit" in combined.lower() or "reset" in combined.lower():
                raise RuntimeError(f"Claude CLI rate limited: {combined}")
            log.warning(f"{args[0]} exited {result.returncode}: stdout={stdout_msg} stderr={stderr_msg}")
    except FileNotFoundError:
        log.warning(f"CLI not found: {args[0]}")
    except subprocess.TimeoutExpired:
        log.warning(f"CLI timed out after 300s: {args[0]}")
    except RuntimeError:
        raise
    except Exception as exc:
        log.debug(f"{args[0]} CLI error: {exc}")
    return None


def _call_via_claude_cli(prompt: str, model: str, files: list[str] | None = None) -> Optional[str]:
    """Claude CLI: `claude -p "prompt" --model <model> [--file f1 --file f2 ...]`"""
    cmd = _which("claude")
    if not cmd:
        return None
    file_args = []
    for f in (files or []):
        file_args.extend(["--file", f])
    return (
        _run_cli([cmd, "-p", prompt, "--model", model] + file_args, prompt)
        or _run_cli([cmd, "--print", "--model", model] + file_args, prompt, stdin_input=prompt)
    )


# ─────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────

def call_llm(
    prompt: str,
    system: str = "",
    model: str = DEFAULT_MODEL,
    image_b64: Optional[str] = None,
    image_file: Optional[str] = None,
) -> str:
    """
    Call Claude CLI and return the response text.

    Note: image_file and image_b64 are NOT supported in CLI subprocess mode.
    The --file flag requires session auth that isn't available in subprocess calls.
    """
    if image_b64 or image_file:
        log.debug("image_b64/image_file ignored — not supported in CLI subprocess mode")

    full_prompt = f"{system}\n\n{prompt}" if system else prompt

    response = _call_via_claude_cli(full_prompt, model)
    if response:
        log.debug("Text call handled by claude CLI")
        return response

    raise RuntimeError(
        "claude CLI is not available. Install Claude Code: "
        "npm install -g @anthropic-ai/claude-code"
    )


call_claude = call_llm  # backward compat alias


def strip_json_fences(text: str) -> str:
    """Remove markdown code fences that LLMs sometimes wrap around JSON."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0].strip()
    return text
