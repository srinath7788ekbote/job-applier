"""
resume_wrapper.py
Tailors a resume to a specific job description using the resume_skill repo.

Flow:
  1. Import parse.py from vendor/resume-skill to extract resume text
  2. Import ATS_RULES + SCHEMA from skill.py
  3. Call Claude (via claude CLI — no API key needed) with resume text + JD + rules
  4. Write tailored JSON to temp file
  5. Subprocess: python skill.py render --data <tmp.json> --output <output.pdf>
  6. Return output_path
"""

import json
import logging
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from claude_client import call_claude, strip_json_fences

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
RESUME_SKILL_DIR = BASE_DIR / "vendor" / "resume-skill"

if str(RESUME_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(RESUME_SKILL_DIR))


def _get_resume_skill_constants() -> tuple[str, str]:
    """Import ATS_RULES and SCHEMA string from vendor skill.py."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "resume_skill", str(RESUME_SKILL_DIR / "skill.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.ATS_RULES, mod.SCHEMA
    except Exception as exc:
        log.warning(f"Could not import ATS_RULES/SCHEMA from skill.py: {exc}")
        return "", ""


def _extract_resume_text(resume_path: str) -> str:
    """Use resume_skill's parse.py to extract text from docx/pdf."""
    try:
        from parse import extract_text
        return extract_text(resume_path)
    except Exception as exc:
        log.warning(f"resume_skill parse.py failed ({exc}), falling back")
        return _fallback_extract(resume_path)


def _fallback_extract(resume_path: str) -> str:
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


def run_resume_skill(
    base_resume_path: str,
    job_description: str,
    output_path: str,
    template: str = "professional",
    resume_skill_dir: Optional[Path] = None,
) -> str:
    """
    Tailor base_resume_path to job_description and render to output_path (PDF).
    Returns output_path on success, raises on failure.
    """
    skill_dir = resume_skill_dir or RESUME_SKILL_DIR
    python = sys.executable

    log.info(f"Extracting resume text from {base_resume_path}")
    resume_text = _extract_resume_text(base_resume_path)

    ats_rules, schema = _get_resume_skill_constants()

    system = (
        "You are an expert resume writer and ATS optimization specialist. "
        "Your task is to tailor a resume to a specific job description. "
        "You must follow the ATS rules provided exactly. "
        "Return ONLY a valid JSON object matching the schema — no markdown fences, "
        "no explanation, no extra text. Every field must be present."
    )

    HARD_RULES = """
=== ABSOLUTE HARD RULES — NEVER VIOLATE THESE ===
These fields must be copied EXACTLY from the original resume. Do NOT change, rephrase, embellish, or add subtitles to any of them:

1. FULL NAME — copy exactly as written
2. EMAIL — copy exactly as written
3. PHONE — copy exactly as written
4. LINKEDIN URL — copy exactly as written
5. GITHUB URL — copy exactly as written
6. JOB TITLES — copy the exact title the person held at each company (e.g. "Site Reliability Engineer"). Never append subtitles, specializations, or descriptors like "— Data Platform Operations". The title must match the original word-for-word.
7. COMPANY NAMES — copy exactly as written (including client names in parentheses)
8. EMPLOYMENT DATES — copy exactly as written (start and end months/years)
9. EDUCATION — degree names, field of study, institution names, and graduation years must be copied exactly
10. CERTIFICATIONS — copy names and codes exactly as written

What you MAY change to tailor to the JD:
- The summary paragraph (reframe tone/focus, never change factual claims)
- Key achievements section (reorder, rename labels, adjust wording — never invent new metrics)
- Skills section (reorder categories, rename category labels, reorder items within categories)
- Experience bullet points (reorder bullets, adjust emphasis, add relevant context — never invent actions or metrics not in the original)

If a field is not in the resume, set it to null. Never invent content.
=== END HARD RULES ===
"""

    prompt = (
        f"Tailor this resume to the job description below.\n\n"
        f"{HARD_RULES}\n"
        f"=== ATS RULES ===\n{ats_rules}\n\n"
        f"=== JSON SCHEMA ===\n{schema}\n\n"
        f"=== CURRENT RESUME TEXT ===\n{resume_text}\n\n"
        f"=== JOB DESCRIPTION ===\n{job_description}\n\n"
        "Return ONLY the JSON object."
    )

    log.info("Calling Claude to tailor resume JSON")
    raw = call_claude(prompt, system=system)
    tailored_data = json.loads(strip_json_fences(raw))

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as tmp:
        json.dump(tailored_data, tmp, indent=2)
        tmp_json_path = tmp.name

    log.info(f"Rendering tailored resume to {output_path}")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    env = {k: v for k, v in __import__("os").environ.items() if k != "CLAUDECODE"}
    result = subprocess.run(
        [
            python, str(skill_dir / "skill.py"),
            "render",
            "--data", tmp_json_path,
            "--output", output_path,
            "--template", template,
        ],
        cwd=str(skill_dir),
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )

    Path(tmp_json_path).unlink(missing_ok=True)

    if result.returncode != 0:
        raise RuntimeError(
            f"resume_skill render failed (exit {result.returncode}):\n{result.stderr[:1000]}"
        )

    log.info(f"Tailored resume saved: {output_path}")
    return output_path
