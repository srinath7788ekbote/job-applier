"""
compare_resume.py
Scores a job description against a resume using Claude.
Returns match score, strengths, gaps, missing ATS keywords, recommendation.
"""

import json
import logging
from pathlib import Path

from claude_client import call_claude, strip_json_fences
from resume_parser import read_resume_text

log = logging.getLogger(__name__)


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
