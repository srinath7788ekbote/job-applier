"""
extract_profile.py
Extracts applicant profile from a resume file using Claude.
Caches result keyed to the resume's last-modified timestamp.

Only call get_profile() from outside this module — never extract_profile() directly.
"""

import json
import logging
import sys
from pathlib import Path

from claude_client import call_claude, strip_json_fences

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
RESUME_SKILL_DIR = BASE_DIR / "vendor" / "resume-skill"
CACHE_FILE = BASE_DIR / "data" / "profile_cache.json"
CACHE_META = BASE_DIR / "data" / "profile_cache.meta.json"

if str(RESUME_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(RESUME_SKILL_DIR))

PROFILE_SCHEMA = """{
  "full_name": "string or null",
  "email": "string or null",
  "phone": "string or null",
  "linkedin_url": "string or null",
  "github_url": "string or null",
  "portfolio_url": "string or null",
  "current_title": "string or null",
  "location": "string or null",
  "years_of_experience": "number or null",
  "work_authorization": "string or null",
  "education": [{"degree": "string", "field": "string", "institution": "string", "year": "string"}],
  "skills": ["string"],
  "languages": ["string"],
  "certifications": ["string"],
  "summary": "string or null"
}"""


def _read_resume_text(resume_path: str) -> str:
    """Extract plain text from resume using resume_skill's parser with fallback."""
    try:
        from parse import extract_text
        return extract_text(resume_path)
    except Exception as exc:
        log.warning(f"vendor parse.py failed ({exc}), using fallback")

    path = Path(resume_path)
    suffix = path.suffix.lower()
    if suffix == ".docx":
        from docx import Document
        doc = Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs)
    elif suffix == ".pdf":
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    return path.read_text(encoding="utf-8", errors="ignore")


def extract_profile(resume_path: str) -> dict:
    """
    Send resume text to Claude and extract structured profile JSON.
    Do not call this directly — use get_profile() for caching.
    """
    resume_text = _read_resume_text(resume_path)

    system = (
        "You are a resume parser. Extract all applicant information from the resume "
        "provided. Return ONLY a valid JSON object with no extra text, no markdown, "
        "no explanation. Use null for any field not explicitly found in the resume. "
        "Never guess or infer values not present in the text."
    )
    prompt = (
        f"Extract from this resume and return JSON with these exact fields:\n"
        f"{PROFILE_SCHEMA}\n\n"
        f"Resume text:\n{resume_text}"
    )

    raw = call_claude(prompt, system=system)
    profile = json.loads(strip_json_fences(raw))
    log.info(f"Profile extracted for: {profile.get('full_name', 'Unknown')}")
    return profile


def get_profile(resume_path: str) -> dict:
    """
    Return cached profile if resume hasn't changed, otherwise re-extract.
    This is the only function the pipeline should call.
    """
    path = Path(resume_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Resume not found at {resume_path}. "
            "Drop your resume (docx or pdf) at data/base_resume.docx"
        )

    current_mtime = path.stat().st_mtime

    if CACHE_FILE.exists() and CACHE_META.exists():
        try:
            meta = json.loads(CACHE_META.read_text(encoding="utf-8"))
            if meta.get("mtime") == current_mtime:
                log.info("Using cached profile (resume unchanged)")
                return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass  # corrupted cache — re-extract

    log.info("Resume changed or no cache — extracting profile via Claude")
    profile = extract_profile(resume_path)

    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    CACHE_META.write_text(json.dumps({"mtime": current_mtime}), encoding="utf-8")

    return profile
