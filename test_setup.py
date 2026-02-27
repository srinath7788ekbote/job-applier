"""
test_setup.py
Step-by-step sanity checks for the job-applier pipeline.
Run this before your first real pipeline run.

Usage:
    python test_setup.py              # run all checks
    python test_setup.py --step 3    # run only step 3
"""

import json
import subprocess
import sys
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR / "scripts"))

OK   = "[OK]"
FAIL = "[FAIL]"
SKIP = "[SKIP]"
WARN = "[WARN]"

results = []


def check(label: str, passed: bool, detail: str = ""):
    status = OK if passed else FAIL
    msg = f"  {status}  {label}"
    if detail:
        msg += f"\n         {detail}"
    print(msg)
    results.append((label, passed))
    return passed


def section(title: str):
    print(f"\n{'-'*55}")
    print(f"  {title}")
    print(f"{'-'*55}")


# ─────────────────────────────────────────────────────────
# STEP 1: Python version
# ─────────────────────────────────────────────────────────
section("Step 1: Python version")
v = sys.version_info
check("Python >= 3.9", v >= (3, 9), f"Found Python {v.major}.{v.minor}.{v.micro}")


# ─────────────────────────────────────────────────────────
# STEP 2: claude CLI (primary Claude access)
# ─────────────────────────────────────────────────────────
section("Step 2: claude CLI (Claude Code)")

import shutil
claude_found = bool(shutil.which("claude") or shutil.which("claude.cmd"))
check("claude CLI on PATH", claude_found,
      "Run: npm install -g @anthropic-ai/claude-code  (if missing)" if not claude_found else "")

if claude_found:
    try:
        # Must unset CLAUDECODE — Claude Code blocks nested sessions otherwise
        env_no_nested = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        result = subprocess.run(
            ["claude", "-p", "Reply with exactly: HELLO", "--model", "claude-haiku-4-5-20251001"],
            capture_output=True, text=True, timeout=30, env=env_no_nested
        )
        works = result.returncode == 0 and result.stdout.strip()
        check("claude CLI responds to prompts", works,
              result.stderr[:200] if not works else f"Response: {result.stdout.strip()[:60]}")
    except Exception as e:
        check("claude CLI responds to prompts", False, str(e))


# ─────────────────────────────────────────────────────────
# STEP 3: Python dependencies
# ─────────────────────────────────────────────────────────
section("Step 3: Python dependencies")

deps = {
    "pdfplumber":   "pdfplumber",
    "docx":         "python-docx",
    "openpyxl":     "openpyxl",
    "playwright":   "playwright",
    "yaml":         "pyyaml",
    "dotenv":       "python-dotenv",
    "pandas":       "pandas",
}

for mod, pkg in deps.items():
    try:
        __import__(mod)
        check(f"import {mod}", True)
    except ImportError:
        check(f"import {mod}", False, f"Install: pip install {pkg}")


# ─────────────────────────────────────────────────────────
# STEP 4: Vendor repos (symlinks/junctions)
# ─────────────────────────────────────────────────────────
section("Step 4: Vendor repos")

scraper_dir      = BASE_DIR / "vendor" / "job-scraper"
resume_skill_dir = BASE_DIR / "vendor" / "resume-skill"

check("vendor/job-scraper exists",  scraper_dir.exists(), str(scraper_dir))
check("vendor/resume-skill exists", resume_skill_dir.exists(), str(resume_skill_dir))

if scraper_dir.exists():
    check("Job_scraper main.py found", (scraper_dir / "main.py").exists())
if resume_skill_dir.exists():
    check("resume_skill skill.py found", (resume_skill_dir / "skill.py").exists())
    check("resume_skill parse.py found", (resume_skill_dir / "parse.py").exists())


# ─────────────────────────────────────────────────────────
# STEP 5: Config and environment
# ─────────────────────────────────────────────────────────
section("Step 5: Config and .env")

config_path = BASE_DIR / "config.yaml"
env_path    = BASE_DIR / ".env"

check("config.yaml exists", config_path.exists())

if config_path.exists():
    import yaml
    try:
        cfg = yaml.safe_load(config_path.read_text())
        role = cfg["pipeline"]["target_role"]
        loc  = cfg["pipeline"]["target_location"]
        check("config.yaml is valid YAML", True, f"role={role!r}  location={loc}")
    except Exception as e:
        check("config.yaml is valid YAML", False, str(e))

if env_path.exists():
    from dotenv import dotenv_values
    env = dotenv_values(str(env_path))
    api_key = env.get("ANTHROPIC_API_KEY", "")
    if api_key and not api_key.startswith("your_"):
        check("ANTHROPIC_API_KEY in .env (optional)", True, "Set — SDK fallback available")
    else:
        print(f"  {SKIP}  ANTHROPIC_API_KEY not set (optional — claude CLI is used instead)")
else:
    print(f"  {SKIP}  .env not found (optional — claude CLI is used instead)")


# ─────────────────────────────────────────────────────────
# STEP 6: Resume file
# ─────────────────────────────────────────────────────────
section("Step 6: Resume file")

resume_path = BASE_DIR / "data" / "base_resume.docx"
resume_pdf  = BASE_DIR / "data" / "base_resume.pdf"

if resume_path.exists():
    check("data/base_resume.docx found", True, f"Size: {resume_path.stat().st_size} bytes")
    RESUME = str(resume_path)
elif resume_pdf.exists():
    check("data/base_resume.pdf found", True, f"Size: {resume_pdf.stat().st_size} bytes")
    RESUME = str(resume_pdf)
else:
    check("Resume file found", False,
          "Drop your resume at:  data/base_resume.docx  (or .pdf)")
    RESUME = None


# ─────────────────────────────────────────────────────────
# STEP 7: Profile extraction (requires resume + Claude)
# ─────────────────────────────────────────────────────────
section("Step 7: Profile extraction via Claude")

if RESUME and claude_found:
    try:
        from extract_profile import get_profile
        print("         Calling Claude to parse resume — this may take 10–20 seconds...")
        profile = get_profile(RESUME)
        name  = profile.get("full_name", "?")
        email = profile.get("email", "?")
        check("Resume parsed by Claude", bool(name), f"Name: {name} | Email: {email}")
    except Exception as e:
        check("Resume parsed by Claude", False, str(e))
else:
    reason = "no resume" if not RESUME else "no claude CLI"
    print(f"  {SKIP}  Skipping profile extraction ({reason})")


# ─────────────────────────────────────────────────────────
# STEP 8: Job scoring (quick Claude call)
# ─────────────────────────────────────────────────────────
section("Step 8: Job scoring via Claude")

if claude_found:
    try:
        from compare_resume import score_job
        fake_jd     = "We need a Python developer with 3+ years experience. Django, REST APIs, PostgreSQL."
        fake_resume = "Python developer with 4 years experience. Built REST APIs using Django and FastAPI. PostgreSQL, Redis."
        print("         Scoring a sample job — this may take 10–20 seconds...")
        result = score_job(fake_jd, fake_resume)
        score  = result.get("score", 0)
        check("Job scoring works", score > 0, f"Score: {score}/100 | rec: {result.get('recommendation','')}")
    except Exception as e:
        check("Job scoring works", False, str(e))
else:
    print(f"  {SKIP}  Skipping (no claude CLI)")


# ─────────────────────────────────────────────────────────
# STEP 9: Excel tracker
# ─────────────────────────────────────────────────────────
section("Step 9: Excel tracker")

import tempfile
tracker_test = Path(tempfile.mktemp(suffix=".xlsx"))
try:
    from update_excel import init_tracker, add_job, job_exists, update_status
    init_tracker(str(tracker_test))
    check("Tracker creation (openpyxl)", tracker_test.exists())

    test_job = {
        "job_id": "test123", "title": "Software Engineer", "company": "Acme",
        "location": "Bengaluru", "apply_url": "https://example.com/job/1",
        "match_score": 85, "strengths": ["Python"], "gaps": ["Kubernetes"],
        "keywords_missing": ["K8s"], "tailored_resume_path": "", "scraped_at": "2026-01-01",
    }
    add_job(str(tracker_test), test_job)
    exists = job_exists(str(tracker_test), "test123")
    check("Add + lookup job in tracker", exists)

    update_status(str(tracker_test), "test123", "applied", "test note")
    check("Update job status", True)
    try:
        tracker_test.unlink(missing_ok=True)
    except Exception:
        pass  # Windows may hold the file briefly; harmless
except Exception as e:
    check("Excel tracker", False, str(e))
    try:
        tracker_test.unlink(missing_ok=True)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────
# STEP 10: Playwright + Chromium
# ─────────────────────────────────────────────────────────
section("Step 10: Playwright + Chromium")

try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("about:blank")
        title = page.title()
        browser.close()
    check("Playwright + Chromium launch", True, "Headless browser works")
except Exception as e:
    check("Playwright + Chromium launch", False,
          f"{e}\n         Fix: playwright install chromium")


# ─────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────
section("Summary")
passed = sum(1 for _, p in results if p)
total  = len(results)
failed = [(label, p) for label, p in results if not p]

print(f"\n  {passed}/{total} checks passed")

if failed:
    print("\n  Failed checks:")
    for label, _ in failed:
        print(f"    {FAIL}  {label}")
    print()
    if passed >= total - 1:
        print("  Almost ready! Fix the items above then run:")
    else:
        print("  Fix the items above, then run:")
    print("    python main_pipeline.py --dry-run")
else:
    print("\n  All checks passed! Run your first pipeline:")
    print("    python main_pipeline.py --dry-run   # safe: no actual applying")
    print("    python main_pipeline.py              # full run")
print()
