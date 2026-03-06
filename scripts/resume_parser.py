"""
resume_parser.py
Shared utility for extracting plain text from resume files (.docx, .pdf, .txt).

Used by: extract_profile.py, compare_resume.py, resume_wrapper.py
"""

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
RESUME_SKILL_DIR = BASE_DIR / "vendor" / "resume-skill"

if str(RESUME_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(RESUME_SKILL_DIR))


def read_resume_text(resume_path: str) -> str:
    """Extract plain text from .docx, .pdf, or .txt resume.

    Tries vendor/resume-skill parse.py first, falls back to direct extraction.
    """
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
