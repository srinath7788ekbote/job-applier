"""
tests/test_claude_client.py
Unit tests for the Claude CLI-only LLM client.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import claude_client


def test_strip_json_fences_plain():
    assert claude_client.strip_json_fences('{"a": 1}') == '{"a": 1}'


def test_strip_json_fences_with_backticks():
    text = "```json\n{\"a\": 1}\n```"
    assert claude_client.strip_json_fences(text) == '{"a": 1}'


def test_strip_json_fences_no_lang():
    text = "```\n{\"a\": 1}\n```"
    assert claude_client.strip_json_fences(text) == '{"a": 1}'


def test_default_model_is_sonnet():
    assert claude_client.DEFAULT_MODEL == "claude-sonnet-4-6"


def test_call_llm_uses_claude_cli(monkeypatch):
    """Claude CLI should be used for text prompts."""
    monkeypatch.setattr(claude_client, "_call_via_claude_cli", lambda *a, **kw: "CLI response")

    result = claude_client.call_llm("test prompt")
    assert result == "CLI response"


def test_call_llm_raises_when_cli_unavailable(monkeypatch):
    """Should raise RuntimeError when claude CLI is not available."""
    monkeypatch.setattr(claude_client, "_call_via_claude_cli", lambda *a, **kw: None)

    with pytest.raises(RuntimeError, match="claude CLI is not available"):
        claude_client.call_llm("test prompt")


def test_call_llm_vision_ignored(monkeypatch):
    """Vision params should be silently ignored in CLI-only mode."""
    monkeypatch.setattr(claude_client, "_call_via_claude_cli", lambda *a, **kw: "ok")
    # Should NOT raise — just ignores the image args
    result = claude_client.call_llm("describe image", image_b64="abc123")
    assert result == "ok"


def test_call_llm_image_file_ignored(monkeypatch):
    """image_file should be silently ignored (CLI subprocess can't upload local files)."""
    monkeypatch.setattr(claude_client, "_call_via_claude_cli", lambda *a, **kw: "ok")
    result = claude_client.call_llm("describe", image_file="/tmp/screenshot.png")
    assert result == "ok"


def test_call_llm_passes_prompt_to_cli(monkeypatch):
    """Prompt should be forwarded to the CLI."""
    captured = {}

    def fake_cli(prompt, model):
        captured["prompt"] = prompt
        return "ok"

    monkeypatch.setattr(claude_client, "_call_via_claude_cli", fake_cli)
    claude_client.call_llm("test prompt")
    assert "test prompt" in captured["prompt"]


def test_call_llm_passes_model(monkeypatch):
    """Model parameter is passed through to claude CLI."""
    captured = {}

    def fake_cli(prompt, model, files=None):
        captured["model"] = model
        return "ok"

    monkeypatch.setattr(claude_client, "_call_via_claude_cli", fake_cli)
    claude_client.call_llm("test", model="claude-opus-4-6")
    assert captured["model"] == "claude-opus-4-6"


def test_call_llm_prepends_system_prompt(monkeypatch):
    """System prompt should be prepended to the prompt."""
    captured = {}

    def fake_cli(prompt, model, files=None):
        captured["prompt"] = prompt
        return "ok"

    monkeypatch.setattr(claude_client, "_call_via_claude_cli", fake_cli)
    claude_client.call_llm("user text", system="system text")
    assert "system text" in captured["prompt"]
    assert "user text" in captured["prompt"]


def test_call_claude_is_alias():
    """call_claude should be an alias for call_llm."""
    assert claude_client.call_claude is claude_client.call_llm
