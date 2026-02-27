"""
compare_resume.py
Scores a job description against a resume using Claude.
Returns match score, strengths, gaps, missing ATS keywords, recommendation.
"""

import json
import logging
import sys
from pathlib import Path

from claude_client import call_claude, strip_json_fences

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
RESUME_SKILL_DIR = BASE_DIR / "vendor" / "resume-skill"

if str(RESUME_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(RESUME_SKILL_DIR))


def read_resume_text(resume_path: str) -> str:
    """Extract plain text from .docx or .pdf resume."""
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


def score_job(job_description: str, resume_text: str) -> dict:
    """
    Score how well the resume matches the job description.
    Returns dict with: score, strengths, gaps, keywords_missing, recommendation.
    On error, returns {"score": 0, "error": <message>}.
    """
    system = "You are an expert technical recruiter and ATS specialist."
    prompt = (
        "Compare this resume against this job description. "
        "Return ONLY a JSON object with no extra text:\n"
        "{\n"
        '  "score": <int 0-100, how well resume matches JD>,\n'
        '  "strengths": [<str>, ...],\n'
        '  "gaps": [<str>, ...],\n'
        '  "keywords_missing": [<str>, ...],\n'
        '  "recommendation": "<apply | tailor and apply | skip>"\n'
        "}\n\n"
        f"Job Description:\n{job_description}\n\n"
        f"Resume:\n{resume_text}"
    )

    try:
        raw = call_claude(prompt, system=system)
        result = json.loads(strip_json_fences(raw))
        result.setdefault("score", 0)
        result.setdefault("strengths", [])
        result.setdefault("gaps", [])
        result.setdefault("keywords_missing", [])
        result.setdefault("recommendation", "")
        return result
    except Exception as exc:
        log.error(f"score_job failed: {exc}")
        return {"score": 0, "error": str(exc)}
