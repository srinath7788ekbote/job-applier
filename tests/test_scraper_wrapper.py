"""
tests/test_scraper_wrapper.py
Unit tests for the job scraper wrapper module.
"""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import scraper_wrapper


def test_make_job_id_deterministic():
    id1 = scraper_wrapper._make_job_id("https://example.com", "Acme", "SRE")
    id2 = scraper_wrapper._make_job_id("https://example.com", "Acme", "SRE")
    assert id1 == id2


def test_make_job_id_length():
    result = scraper_wrapper._make_job_id("https://example.com", "Acme", "SRE")
    assert len(result) == 16


def test_make_job_id_uses_sha256():
    import hashlib
    raw = "https://example.comacmesre"
    expected = hashlib.sha256(raw.encode()).hexdigest()[:16]
    result = scraper_wrapper._make_job_id("https://example.com", "Acme", "SRE")
    assert result == expected


def test_make_job_id_case_insensitive():
    id1 = scraper_wrapper._make_job_id("https://example.com", "ACME", "SRE")
    id2 = scraper_wrapper._make_job_id("https://example.com", "acme", "sre")
    assert id1 == id2


def test_normalize_maps_fields():
    raw = {
        "url": "https://example.com/job/1",
        "company": "Acme Corp",
        "title": "SRE",
        "location": "Dubai",
        "description": "Job desc",
        "platform": "linkedin",
    }
    result = scraper_wrapper._normalize(raw, "2026-01-01T00:00:00Z")
    assert result["title"] == "SRE"
    assert result["company"] == "Acme Corp"
    assert result["location"] == "Dubai"
    assert result["apply_url"] == "https://example.com/job/1"
    assert result["scraped_at"] == "2026-01-01T00:00:00Z"
    assert result["platform"] == "linkedin"


def test_normalize_missing_fields():
    raw = {}
    result = scraper_wrapper._normalize(raw, "2026-01-01T00:00:00Z")
    assert result["title"] is None
    assert result["company"] is None
    assert result["location"] is None
    assert result["apply_url"] is None
    assert result["description"] is None


def test_run_scraper_returns_empty_on_timeout(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        MagicMock(side_effect=subprocess.TimeoutExpired(cmd="test", timeout=300)),
    )
    result = scraper_wrapper.run_scraper("SRE", ["Dubai"], 10)
    assert result == []


def test_run_scraper_returns_empty_on_nonzero_exit(monkeypatch):
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = "error"
    monkeypatch.setattr(subprocess, "run", MagicMock(return_value=mock_result))
    result = scraper_wrapper.run_scraper("SRE", ["Dubai"], 10)
    assert result == []


def test_run_scraper_returns_normalized_jobs(monkeypatch, tmp_path):
    scraper_dir = tmp_path / "scraper"
    scraper_dir.mkdir()
    (scraper_dir / "main.py").write_text("pass")

    jobs_data = [
        {"url": "https://example.com/1", "company": "Acme", "title": "SRE", "location": "Dubai"},
        {"url": "https://example.com/2", "company": "Corp", "title": "DevOps", "location": "Sydney"},
    ]

    def fake_run(cmd, **kwargs):
        # Write the JSON output where the wrapper expects it
        output_stem = None
        for i, arg in enumerate(cmd):
            if arg == "--output":
                output_stem = cmd[i + 1]
                break
        json_file = Path(output_stem + ".json")
        json_file.write_text(json.dumps(jobs_data), encoding="utf-8")
        mock = MagicMock()
        mock.returncode = 0
        return mock

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = scraper_wrapper.run_scraper("SRE", ["Dubai"], 10, scraper_dir=scraper_dir)
    assert len(result) == 2
    assert result[0]["title"] == "SRE"
    assert result[1]["title"] == "DevOps"
    assert len(result[0]["job_id"]) == 16
