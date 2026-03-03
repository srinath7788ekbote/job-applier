"""
tests/test_update_excel.py
Unit tests for the Excel tracker module.
"""
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from update_excel import init_tracker, add_job, job_exists, update_status, get_pending_jobs

SAMPLE_JOB = {
    "job_id": "abc123",
    "title": "Site Reliability Engineer",
    "company": "Acme Corp",
    "location": "Dubai, UAE",
    "apply_url": "https://example.com/jobs/1",
    "match_score": 80,
    "strengths": ["Python", "Kubernetes"],
    "gaps": ["Hadoop"],
    "keywords_missing": ["Spark"],
    "tailored_resume_path": "/data/tailored/abc123.pdf",
    "scraped_at": "2026-03-03",
    "status": "pending",
}


@pytest.fixture
def tracker(tmp_path):
    path = str(tmp_path / "test_tracker.xlsx")
    init_tracker(path)
    return path


def test_init_creates_file(tmp_path):
    path = str(tmp_path / "tracker.xlsx")
    init_tracker(path)
    assert Path(path).exists()


def test_add_and_lookup_job(tracker):
    add_job(tracker, SAMPLE_JOB)
    assert job_exists(tracker, "abc123")


def test_job_not_exists(tracker):
    assert not job_exists(tracker, "nonexistent")


def test_add_duplicate_job_no_error(tracker):
    add_job(tracker, SAMPLE_JOB)
    add_job(tracker, SAMPLE_JOB)  # should not raise


def test_update_status(tracker):
    add_job(tracker, SAMPLE_JOB)
    update_status(tracker, "abc123", "applied", "Applied via careers portal")
    jobs = get_pending_jobs(tracker)
    # abc123 should no longer be pending
    ids = [j["job_id"] for j in jobs]
    assert "abc123" not in ids


def test_get_pending_jobs_empty(tracker):
    assert get_pending_jobs(tracker) == []


def test_get_pending_jobs_multiple(tracker):
    for i in range(3):
        job = {**SAMPLE_JOB, "job_id": f"job{i}", "title": f"SRE {i}"}
        add_job(tracker, job)
    jobs = get_pending_jobs(tracker)
    assert len(jobs) == 3
