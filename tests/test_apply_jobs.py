"""
tests/test_apply_jobs.py
Unit tests for apply_jobs helpers (no browser launched).
"""
import sys
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import apply_jobs


def test_detect_linkedin_auth_wall_true():
    page = MagicMock()
    # Not logged in (no nav element), on login page
    page.url = "https://www.linkedin.com/login"

    def locator_side_effect(selector):
        mock = MagicMock()
        if "global-nav" in selector or "data-test-global-nav" in selector:
            mock.count.return_value = 0  # not logged in
        else:
            mock.count.return_value = 1  # auth wall selector matches
        return mock

    page.locator.side_effect = locator_side_effect
    assert apply_jobs._detect_linkedin_auth_wall(page) is True


def test_detect_linkedin_auth_wall_false():
    page = MagicMock()
    page.url = "https://www.linkedin.com/jobs/view/123"

    def locator_side_effect(selector):
        mock = MagicMock()
        if "global-nav" in selector or "data-test-global-nav" in selector:
            mock.count.return_value = 1  # logged in
        else:
            mock.count.return_value = 0
        return mock

    page.locator.side_effect = locator_side_effect
    assert apply_jobs._detect_linkedin_auth_wall(page) is False


def test_detect_linkedin_auth_wall_authwall_url():
    page = MagicMock()
    page.url = "https://www.linkedin.com/authwall"

    def locator_side_effect(selector):
        mock = MagicMock()
        if "global-nav" in selector or "data-test-global-nav" in selector:
            mock.count.return_value = 0  # not logged in
        else:
            mock.count.return_value = 0  # doesn't matter, URL check is enough
        return mock

    page.locator.side_effect = locator_side_effect
    assert apply_jobs._detect_linkedin_auth_wall(page) is True


def test_extract_external_apply_url_returns_none_when_not_found():
    page = MagicMock()
    loc = MagicMock()
    loc.count.return_value = 0
    page.locator.return_value.first = loc
    result = apply_jobs._extract_external_apply_url(page)
    assert result is None


def test_run_application_returns_agent_handoff_when_no_cli(monkeypatch, tmp_path):
    """With no CLI available and no cookies, should return agent_handoff_required."""
    # Patch the CLI calls to fail
    monkeypatch.setattr(apply_jobs, "_call_via_claude_cli", lambda *a, **kw: None, raising=False)

    # Patch playwright to avoid real browser — mock the persistent context API
    mock_context = MagicMock()
    mock_page = MagicMock()
    mock_page.url = "https://www.linkedin.com/uas/login"
    mock_context.pages = [mock_page]

    fake_resume = tmp_path / "resume.pdf"
    fake_resume.write_bytes(b"%PDF fake")

    profile = {"full_name": "Srinath Ekbote", "email": "test@example.com"}

    with patch("apply_jobs.sync_playwright") as mock_pw:
        mock_pw.return_value.__enter__.return_value.chromium.launch_persistent_context.return_value = mock_context
        # Patch auth wall detection to return True immediately
        with patch.object(apply_jobs, "_detect_linkedin_auth_wall", return_value=True):
            with patch.object(apply_jobs, "_extract_external_apply_url", return_value=None):
                with patch.object(apply_jobs, "apply_external_form",
                                  return_value={"success": False, "method": "agent_handoff_required",
                                                "url": "https://example.com/apply"}):
                    result = apply_jobs.run_application(
                        job={"title": "SRE", "company": "Acme", "apply_url": "https://ae.linkedin.com/jobs/123"},
                        resume_path=str(fake_resume),
                        profile=profile,
                        headless=True,
                        slow_mo=0,
                        min_delay=0,
                        max_delay=0,
                    )

    assert result["method"] == "agent_handoff_required"
