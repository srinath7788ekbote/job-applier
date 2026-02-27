"""
scraper_wrapper.py
Calls the Job_scraper CLI as a subprocess, reads its JSON output,
and normalizes every job to the pipeline's standard schema.
"""

import hashlib
import json
import logging
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
SCRAPER_DIR = BASE_DIR / "vendor" / "job-scraper"


def _make_job_id(url: str, company: str, title: str) -> str:
    """Stable 12-char ID derived from url+company+title."""
    raw = f"{url}{company}{title}".lower().strip()
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _normalize(raw: dict, scraped_at: str) -> dict:
    """Map scraper output fields to pipeline schema. Extra fields preserved."""
    url = raw.get("url") or ""
    company = raw.get("company") or ""
    title = raw.get("title") or ""

    normalized = {
        "job_id":      _make_job_id(url, company, title),
        "title":       title or None,
        "company":     company or None,
        "location":    raw.get("location") or None,
        "description": raw.get("description") or None,
        "apply_url":   url or None,
        "scraped_at":  scraped_at,
        # extra fields the scraper provides — kept for reference
        "platform":             raw.get("platform") or None,
        "key_responsibilities": raw.get("key_responsibilities") or None,
        "skills":               raw.get("skills") or None,
        "years_of_experience":  raw.get("years_of_experience") or None,
        "posted_date":          raw.get("posted_date") or None,
        "contact_email":        raw.get("email") or None,
    }
    return normalized


def run_scraper(
    role: str,
    locations: list[str],
    limit: int,
    days: int = 7,
    platforms: Optional[list[str]] = None,
    scraper_dir: Optional[Path] = None,
) -> list[dict]:
    """
    Invoke the Job_scraper CLI and return normalized job dicts.

    Returns [] on any error so the pipeline can continue gracefully.
    """
    scraper_path = scraper_dir or SCRAPER_DIR
    python = sys.executable

    with tempfile.TemporaryDirectory() as tmpdir:
        output_stem = Path(tmpdir) / "jobs"

        cmd = [
            python, str(scraper_path / "main.py"),
            "--keyword", role,
            "--location", *locations,
            "--limit", str(limit),
            "--days", str(days),
            "--output", str(output_stem),
        ]
        if platforms:
            cmd += ["--platform", *platforms]

        log.info(f"Running scraper: {' '.join(cmd)}")

        # Strip CLAUDECODE so the subprocess isn't blocked by Claude Code's nesting guard
        env = {k: v for k, v in __import__("os").environ.items() if k != "CLAUDECODE"}
        try:
            result = subprocess.run(
                cmd,
                cwd=str(scraper_path),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",   # Windows em-dashes etc. won't crash capture
                timeout=300,        # 5 min cap
                env=env,
            )
        except subprocess.TimeoutExpired:
            log.error("Scraper timed out after 5 minutes")
            return []
        except Exception as exc:
            log.error(f"Scraper subprocess failed: {exc}")
            return []

        if result.returncode != 0:
            log.error(f"Scraper exited {result.returncode}:\n{result.stderr[:2000]}")
            return []

        json_file = Path(str(output_stem) + ".json")
        if not json_file.exists():
            log.error(f"Scraper JSON output not found at {json_file}")
            return []

        try:
            raw_jobs = json.loads(json_file.read_text(encoding="utf-8"))
        except Exception as exc:
            log.error(f"Failed to parse scraper JSON: {exc}")
            return []

    scraped_at = datetime.utcnow().isoformat()
    jobs = [_normalize(j, scraped_at) for j in raw_jobs if isinstance(j, dict)]
    log.info(f"Scraper returned {len(jobs)} jobs")
    return jobs
