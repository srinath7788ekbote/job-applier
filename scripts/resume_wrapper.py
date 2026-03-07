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

from claude_client import call_llm, strip_json_fences
from resume_parser import read_resume_text

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
RESUME_SKILL_DIR = BASE_DIR / "vendor" / "resume-skill"


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
    resume_text = read_resume_text(base_resume_path)

    ats_rules, schema = _get_resume_skill_constants()

    system = (
        "You are an expert technical resume writer and ATS optimization specialist. "
        "Your task is to tailor an IT engineering professional's resume to a specific job description. "
        "You must follow the ATS and formatting rules provided exactly. "
        "Return ONLY a valid JSON object matching the schema — no markdown fences, "
        "no explanation, no extra text. Every field must be present."
    )

    RULES = """
=== ABSOLUTE HARD RULES — NEVER VIOLATE THESE ===
These fields must be copied EXACTLY from the original resume. Do NOT change, rephrase, embellish, or add subtitles to any of them:
1. FULL NAME — copy exactly as written
2. EMAIL — copy exactly as written
3. PHONE — copy exactly as written
4. LINKEDIN URL — copy exactly as written
5. GITHUB URL/PORTFOLIO — copy exactly as written
6. JOB TITLES — copy the exact title held at each company. Never append subtitles, specializations, or descriptors.
7. COMPANY NAMES — copy exactly as written
8. EMPLOYMENT DATES — copy exactly as written
9. EDUCATION — degree names, field of study, institution names, graduation years
10. CERTIFICATIONS — copy names and codes exactly as written

=== ANTI-AI DETECTION & TONE RULES ===
To bypass AI detectors and appeal to engineering hiring managers, you must strictly adhere to this writing style:
1. BAN LIST: Never use the following words: delve, spearhead, synergize, testament, dynamic, multifaceted, elevate, seamless, pivotal, landscape, navigate, foster, unlock, unleash, transformative.
2. TONE: Write in a terse, direct, highly technical, and objective tone. Do not use filler words, adverbs, or subjective adjectives (e.g., "successfully," "expertly," "impressive," "cutting-edge").
3. METRICS: You must preserve the original metrics, percentages, data volumes, and time-savings EXACTLY as they appear. Never invent or estimate new numbers.
4. BULLET STRUCTURE: Keep bullet points under 2 lines. Start strictly with a strong past-tense engineering verb (e.g., Architected, Engineered, Developed, Migrated, Deployed, Automated, Optimized).

=== ATS OPTIMIZATION RULES ===
1. EXACT MATCHING: Identify hard technical skills in the Job Description (e.g., programming languages, frameworks, databases, cloud providers, infrastructure tools). If the candidate possesses these skills based on the original resume, mirror the EXACT terminology and acronyms used in the JD.
2. NO KEYWORD STUFFING: Integrate JD keywords naturally into the existing experience bullets and skills section. Do not arbitrarily list keywords out of context.

What you MAY change to tailor to the JD:
- Summary paragraph (reframe focus toward JD priorities, utilizing JD terminology, never change factual claims)
- Key achievements section (reorder based on JD relevance, adjust wording to highlight JD-specific technologies)
- Skills section (reorder categories, rename category labels to match JD, reorder items)
- Experience bullet points (reorder to put the most JD-relevant bullets first, adjust context to highlight overlapping technologies — never invent experience)
- layout_order array: define the strategic section order based on JD relevance and candidate strengths (e.g., if the JD emphasizes project experience, move "projects" above "experience"; push "awards" to the bottom for senior candidates)

If a field is not in the resume, set it to null. Never invent content.
=== END RULES ===
"""

    prompt = (
        f"Tailor this resume to the job description below.\n\n"
        f"{RULES}\n"
        f"=== ATS RULES ===\n{ats_rules}\n\n"
        f"=== JSON SCHEMA ===\n{schema}\n\n"
        f"=== CURRENT RESUME TEXT ===\n{resume_text}\n\n"
        f"=== JOB DESCRIPTION ===\n{job_description}\n\n"
        "Return ONLY the JSON object."
    )

    log.info("Calling LLM to tailor resume JSON")
    raw = call_llm(prompt, system=system)
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
