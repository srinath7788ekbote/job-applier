"""
tests/test_claude_client.py
Unit tests for the LLM client fallback chain.
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

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


def test_call_claude_uses_nvidia_nim_when_cli_unavailable(monkeypatch):
    """When both CLIs fail, NVIDIA NIM should be called."""
    monkeypatch.setattr(claude_client, "_call_via_claude_cli", lambda *a: None)
    monkeypatch.setattr(claude_client, "_call_via_openclaw_cli", lambda *a: None)
    monkeypatch.setattr(claude_client, "_call_via_nvidia_nim", lambda *a: "NIM response")

    result = claude_client.call_claude("test prompt")
    assert result == "NIM response"


def test_call_claude_prefers_claude_cli(monkeypatch):
    """claude CLI should be tried first."""
    monkeypatch.setattr(claude_client, "_call_via_claude_cli", lambda *a: "CLI response")
    monkeypatch.setattr(claude_client, "_call_via_openclaw_cli", lambda *a: "openclaw response")
    monkeypatch.setattr(claude_client, "_call_via_nvidia_nim", lambda *a: "NIM response")

    result = claude_client.call_claude("test prompt")
    assert result == "CLI response"


def test_call_claude_falls_back_to_anthropic_sdk(monkeypatch):
    """Falls back to Anthropic SDK if all else fails."""
    monkeypatch.setattr(claude_client, "_call_via_claude_cli", lambda *a: None)
    monkeypatch.setattr(claude_client, "_call_via_openclaw_cli", lambda *a: None)
    monkeypatch.setattr(claude_client, "_call_via_nvidia_nim", lambda *a: (_ for _ in ()).throw(RuntimeError("NIM down")))
    monkeypatch.setattr(claude_client, "_call_via_anthropic_sdk", lambda *a, **kw: "SDK response")

    result = claude_client.call_claude("test prompt")
    assert result == "SDK response"


def test_nvidia_nim_raises_on_bad_response(monkeypatch):
    """_call_via_nvidia_nim should raise if API returns unexpected shape."""
    import urllib.request

    class FakeResponse:
        def read(self):
            return b'{"choices": []}'  # empty choices
        def __enter__(self): return self
        def __exit__(self, *a): pass

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: FakeResponse())
    with pytest.raises((KeyError, IndexError)):
        claude_client._call_via_nvidia_nim("test", "")
