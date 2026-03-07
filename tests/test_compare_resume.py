"""
tests/test_compare_resume.py
Unit tests for job scoring (mocks LLM call).
"""
import sys
import json
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import compare_resume


MOCK_SCORE_RESPONSE = json.dumps({
    "score": 82,
    "recommendation": "tailor and apply",
    "strengths": ["Python", "Kubernetes", "Azure"],
    "gaps": ["Hadoop"],
    "keywords_missing": ["Spark"],
})


def test_score_job_returns_dict(monkeypatch):
    monkeypatch.setattr(compare_resume, "call_llm", lambda *a, **kw: MOCK_SCORE_RESPONSE)
    result = compare_resume.score_job("some job description", "some resume text")
    assert isinstance(result, dict)
    assert result["score"] == 82


def test_score_job_has_required_keys(monkeypatch):
    monkeypatch.setattr(compare_resume, "call_llm", lambda *a, **kw: MOCK_SCORE_RESPONSE)
    result = compare_resume.score_job("jd", "resume")
    for key in ["score", "recommendation", "strengths", "gaps", "keywords_missing"]:
        assert key in result, f"Missing key: {key}"


def test_score_job_handles_malformed_llm_response(monkeypatch):
    """If LLM returns garbage, score_job should not crash — return score=0."""
    monkeypatch.setattr(compare_resume, "call_llm", lambda *a, **kw: "not json at all")
    result = compare_resume.score_job("jd", "resume")
    assert isinstance(result, dict)
    assert result.get("score", 0) == 0


def test_score_job_handles_json_in_fences(monkeypatch):
    fenced = f"```json\n{MOCK_SCORE_RESPONSE}\n```"
    monkeypatch.setattr(compare_resume, "call_llm", lambda *a, **kw: fenced)
    result = compare_resume.score_job("jd", "resume")
    assert result["score"] == 82
