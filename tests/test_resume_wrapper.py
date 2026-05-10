"""
tests/test_resume_wrapper.py
Unit tests for resume tailoring wrapper (mocks LLM and subprocess).
"""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import resume_wrapper


MOCK_TAILORED_JSON = json.dumps({
    "full_name": "Srinath Ekbote",
    "summary": "Experienced SRE",
    "skills": {"Cloud": ["AWS", "Azure"]},
})


def test_get_resume_skill_constants_returns_empty_on_missing():
    with patch.object(resume_wrapper, "RESUME_SKILL_DIR", Path("/nonexistent")):
        ats_rules, schema = resume_wrapper._get_resume_skill_constants()
        assert ats_rules == ""
        assert schema == ""


def test_run_resume_skill_returns_output_path(monkeypatch, tmp_path):
    output_path = str(tmp_path / "output" / "resume.pdf")

    monkeypatch.setattr(resume_wrapper, "read_resume_text", lambda path: "resume text")
    monkeypatch.setattr(resume_wrapper, "_get_resume_skill_constants", lambda: ("rules", "schema"))
    monkeypatch.setattr(resume_wrapper, "call_llm", lambda *a, **kw: MOCK_TAILORED_JSON)

    mock_result = MagicMock()
    mock_result.returncode = 0
    monkeypatch.setattr(subprocess, "run", MagicMock(return_value=mock_result))

    result = resume_wrapper.run_resume_skill(
        base_resume_path="data/resume.pdf",
        job_description="Looking for an SRE",
        output_path=output_path,
        resume_skill_dir=tmp_path,
    )
    assert result == output_path
    assert Path(output_path).parent.exists()


def test_run_resume_skill_raises_on_subprocess_failure(monkeypatch, tmp_path):
    output_path = str(tmp_path / "resume.pdf")

    monkeypatch.setattr(resume_wrapper, "read_resume_text", lambda path: "resume text")
    monkeypatch.setattr(resume_wrapper, "_get_resume_skill_constants", lambda: ("", ""))
    monkeypatch.setattr(resume_wrapper, "call_llm", lambda *a, **kw: MOCK_TAILORED_JSON)

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = "render error"
    monkeypatch.setattr(subprocess, "run", MagicMock(return_value=mock_result))

    with pytest.raises(RuntimeError, match="resume_skill render failed"):
        resume_wrapper.run_resume_skill(
            base_resume_path="data/resume.pdf",
            job_description="Looking for an SRE",
            output_path=output_path,
            resume_skill_dir=tmp_path,
        )


def test_run_resume_skill_raises_on_bad_llm_response(monkeypatch, tmp_path):
    output_path = str(tmp_path / "resume.pdf")

    monkeypatch.setattr(resume_wrapper, "read_resume_text", lambda path: "resume text")
    monkeypatch.setattr(resume_wrapper, "_get_resume_skill_constants", lambda: ("", ""))
    monkeypatch.setattr(resume_wrapper, "call_llm", lambda *a, **kw: "not json at all")

    with pytest.raises(Exception):
        resume_wrapper.run_resume_skill(
            base_resume_path="data/resume.pdf",
            job_description="Looking for an SRE",
            output_path=output_path,
            resume_skill_dir=tmp_path,
        )
