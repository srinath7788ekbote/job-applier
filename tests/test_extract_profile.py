"""
tests/test_extract_profile.py
Unit tests for resume profile extraction (mocks LLM call).
"""
import sys
import json
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import extract_profile

MOCK_PROFILE = {
    "full_name": "Srinath Ekbote",
    "email": "ekbotesrinath@gmail.com",
    "phone": "+91 9916843394",
    "location": "Bengaluru, India",
    "title": "Site Reliability Engineer",
    "years_experience": 6,
    "skills": ["Python", "Kubernetes", "Azure", "Terraform"],
    "education": [{"degree": "MSc IT Security Management", "institution": "Arden University"}],
    "experience": [],
    "certifications": ["AZ-400", "AZ-104"],
    "languages": ["English"],
    "summary": "Experienced SRE with 6 years in cloud-native engineering.",
}


def test_parse_profile_from_llm_response(monkeypatch, tmp_path):
    fake_resume = tmp_path / "resume.pdf"
    fake_resume.write_bytes(b"%PDF fake")
    monkeypatch.setattr(extract_profile, "call_claude", lambda *a, **kw: json.dumps(MOCK_PROFILE))
    monkeypatch.setattr(extract_profile, "_read_resume_text", lambda path: "resume text")
    result = extract_profile.extract_profile(str(fake_resume))
    assert result["full_name"] == "Srinath Ekbote"
    assert result["email"] == "ekbotesrinath@gmail.com"


def test_parse_profile_handles_fenced_json(monkeypatch, tmp_path):
    fake_resume = tmp_path / "resume.pdf"
    fake_resume.write_bytes(b"%PDF fake")
    fenced = f"```json\n{json.dumps(MOCK_PROFILE)}\n```"
    monkeypatch.setattr(extract_profile, "call_claude", lambda *a, **kw: fenced)
    monkeypatch.setattr(extract_profile, "_read_resume_text", lambda path: "resume text")
    result = extract_profile.extract_profile(str(fake_resume))
    assert result["full_name"] == "Srinath Ekbote"


def test_parse_profile_handles_bad_response(monkeypatch, tmp_path):
    fake_resume = tmp_path / "resume.pdf"
    fake_resume.write_bytes(b"%PDF fake")
    monkeypatch.setattr(extract_profile, "call_claude", lambda *a, **kw: "not json")
    monkeypatch.setattr(extract_profile, "_read_resume_text", lambda path: "resume text")
    with pytest.raises(Exception):
        extract_profile.extract_profile(str(fake_resume))
