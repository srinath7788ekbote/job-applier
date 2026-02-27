"""
main_pipeline.py
Orchestrates the full job application pipeline:
  1. Guard: skip if already ran successfully today
  2. Extract applicant profile from resume (cached)
  3. Scrape jobs
  4. Filter already-seen jobs
  5. Score each job against resume
  6. Tailor resume with Claude
  7. Log to Excel tracker
  8. Apply via browser automation
  9. Mark run complete

Usage:
  python main_pipeline.py              # full run
  python main_pipeline.py --dry-run    # scrape + score + tailor, no browser apply
"""

import logging
import random
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml
from dotenv import load_dotenv

# ------------------------------------------------------------------
# Bootstrap
# ------------------------------------------------------------------
load_dotenv()
BASE_DIR = Path(__file__).parent

# Add scripts/ to path
sys.path.insert(0, str(BASE_DIR / "scripts"))

from scraper_wrapper  import run_scraper
from resume_wrapper   import run_resume_skill
from extract_profile  import get_profile
from compare_resume   import score_job, read_resume_text
from update_excel     import (
    init_tracker, job_exists, add_job, update_status,
    STATUS_PENDING, STATUS_APPLIED, STATUS_FAILED, STATUS_SKIPPED, STATUS_MANUAL,
)
from apply_jobs import run_application


# ------------------------------------------------------------------
# Config
# ------------------------------------------------------------------
@dataclass
class Config:
    target_role:          str
    target_location:      list
    target_platforms:     list
    max_jobs_per_run:     int
    days_back:            int
    min_match_score:      int
    resume_template:      str
    base_resume:          Path
    excel_tracker:        Path
    tailored_resumes_dir: Path
    scraper_vendor:       Path
    resume_skill_vendor:  Path
    logs_dir:             Path
    headless:             bool
    slow_mo:              int
    min_delay:            float
    max_delay:            float


def load_config(config_path: Path) -> Config:
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    pipe = raw["pipeline"]
    paths = raw["paths"]
    pw = raw["playwright"]

    def p(rel: str) -> Path:
        return (BASE_DIR / rel).resolve()

    location = pipe["target_location"]
    if isinstance(location, str):
        location = [location]

    return Config(
        target_role=pipe["target_role"],
        target_location=location,
        target_platforms=pipe.get("target_platforms", ["linkedin"]),
        max_jobs_per_run=pipe["max_jobs_per_run"],
        days_back=pipe.get("days_back", 7),
        min_match_score=pipe["min_match_score"],
        resume_template=pipe.get("resume_template", "professional"),
        base_resume=p(paths["base_resume"]),
        excel_tracker=p(paths["excel_tracker"]),
        tailored_resumes_dir=p(paths["tailored_resumes_dir"]),
        scraper_vendor=p(paths["scraper_vendor"]),
        resume_skill_vendor=p(paths["resume_skill_vendor"]),
        logs_dir=p(paths["logs_dir"]),
        headless=pw.get("headless", True),
        slow_mo=pw.get("slow_mo", 50),
        min_delay=pw.get("min_delay", 1.5),
        max_delay=pw.get("max_delay", 4.0),
    )


# ------------------------------------------------------------------
# Run-guard (prevent duplicate runs on same day)
# ------------------------------------------------------------------
def _ran_dates_file(logs_dir: Path) -> Path:
    return logs_dir / "ran_dates.txt"


def already_ran_today(logs_dir: Path) -> bool:
    f = _ran_dates_file(logs_dir)
    if not f.exists():
        return False
    today = str(date.today())
    return today in f.read_text(encoding="utf-8")


def mark_ran_today(logs_dir: Path) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    f = _ran_dates_file(logs_dir)
    today = str(date.today())
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(today + "\n")


# ------------------------------------------------------------------
# Logging setup
# ------------------------------------------------------------------
def setup_logging(logs_dir: Path) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / f"pipeline_{date.today()}.log"

    handlers = [
        logging.FileHandler(str(log_file), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )


# ------------------------------------------------------------------
# Pipeline
# ------------------------------------------------------------------
def run_pipeline(dry_run: bool = False, overrides: dict = {}) -> None:
    config = load_config(BASE_DIR / "config.yaml")

    # Apply CLI overrides (any non-None value from argparse replaces config)
    if overrides.get("role"):
        config.target_role = overrides["role"]
    if overrides.get("location"):
        config.target_location = overrides["location"]
    if overrides.get("days") is not None:
        config.days_back = overrides["days"]
    if overrides.get("platform"):
        config.target_platforms = overrides["platform"]
    if overrides.get("max_jobs") is not None:
        config.max_jobs_per_run = overrides["max_jobs"]
    if overrides.get("min_score") is not None:
        config.min_match_score = overrides["min_score"]
    if overrides.get("template"):
        config.resume_template = overrides["template"]
    if overrides.get("resume"):
        config.base_resume = Path(overrides["resume"]).resolve()
    setup_logging(config.logs_dir)
    log = logging.getLogger("pipeline")

    log.info(f"{'=' * 60}")
    log.info(f"Pipeline started {'(DRY RUN) ' if dry_run else ''}| role={config.target_role} | loc={config.target_location}")
    log.info(f"{'=' * 60}")

    # Guard
    if already_ran_today(config.logs_dir):
        log.info("Already ran successfully today — exiting")
        return

    # Validate resume
    if not config.base_resume.exists():
        log.error(
            f"Resume not found at {config.base_resume}. "
            "Drop your resume (docx or pdf) at data/base_resume.docx and retry."
        )
        return

    # Init tracker
    init_tracker(str(config.excel_tracker))

    # Step 1 — Extract profile
    log.info("Step 1: Extracting applicant profile from resume")
    try:
        profile = get_profile(str(config.base_resume))
        log.info(f"Profile loaded for: {profile.get('full_name', 'Unknown')}")
    except Exception as exc:
        log.error(f"Profile extraction failed: {exc}")
        return

    # Step 2 — Scrape jobs
    log.info("Step 2: Scraping jobs")
    jobs = run_scraper(
        role=config.target_role,
        locations=config.target_location,
        limit=config.max_jobs_per_run * 3,   # scrape more, filter down
        days=config.days_back,
        platforms=config.target_platforms,
        scraper_dir=config.scraper_vendor,
    )
    log.info(f"Scraped {len(jobs)} jobs total")

    if not jobs:
        log.warning("No jobs scraped — check scraper logs or network")
        mark_ran_today(config.logs_dir)
        return

    # Step 3 — Filter already seen
    new_jobs = [j for j in jobs if not job_exists(str(config.excel_tracker), j["job_id"])]
    log.info(f"{len(new_jobs)} new (unseen) jobs")

    if not new_jobs:
        log.info("No new jobs to process today")
        mark_ran_today(config.logs_dir)
        return

    # Read resume text once (used for scoring all jobs)
    resume_text = read_resume_text(str(config.base_resume))

    processed = 0
    for job in new_jobs:
        if processed >= config.max_jobs_per_run:
            break

        title   = job.get("title", "?")
        company = job.get("company", "?")
        jid     = job["job_id"]
        log.info(f"--- Processing: {title} @ {company} (id={jid}) ---")

        # Step 4 — Score
        log.info("Step 4: Scoring job match")
        score_result = score_job(job.get("description", ""), resume_text)
        score = score_result.get("score", 0)
        log.info(
            f"Score: {score}/100 — {score_result.get('recommendation', '')} | "
            f"gaps: {score_result.get('gaps', [])[:2]}"
        )

        if score < config.min_match_score:
            log.info(f"Score {score} < threshold {config.min_match_score} — skipping")
            add_job(str(config.excel_tracker), {
                **job,
                "match_score":        score,
                "strengths":          score_result.get("strengths", []),
                "gaps":               score_result.get("gaps", []),
                "keywords_missing":   score_result.get("keywords_missing", []),
                "tailored_resume_path": "",
                "status":             STATUS_SKIPPED,
                "notes":              f"Score below threshold ({score} < {config.min_match_score})",
            })
            processed += 1
            continue

        # Step 5 — Tailor resume
        log.info("Step 5: Tailoring resume with Claude")
        # Filename: First_Last_Company.pdf
        name_parts = profile.get("full_name", "Applicant").split()
        name_slug = "_".join(name_parts)                                      # "Srinath_Ekbote"
        safe_company = "_".join(
            w for w in "".join(
                c if c.isalnum() or c == " " else " " for c in company
            ).split()
        )[:30]                                                                 # "Halian" (max 30 chars)
        output_filename = f"{name_slug}_{safe_company}.pdf"
        output_path = str(config.tailored_resumes_dir / output_filename)

        try:
            tailor_result = run_resume_skill(
                base_resume_path=str(config.base_resume),
                job_description=job.get("description", ""),
                output_path=output_path,
                template=config.resume_template,
                resume_skill_dir=config.resume_skill_vendor,
            )
            log.info(f"Tailored resume: {tailor_result}")
        except Exception as exc:
            log.error(f"Resume tailoring failed: {exc} — using base resume")
            tailor_result = str(config.base_resume)

        # Step 6 — Log to Excel as pending
        add_job(str(config.excel_tracker), {
            **job,
            "match_score":          score,
            "strengths":            score_result.get("strengths", []),
            "gaps":                 score_result.get("gaps", []),
            "keywords_missing":     score_result.get("keywords_missing", []),
            "tailored_resume_path": tailor_result,
            "status":               STATUS_PENDING,
        })

        if dry_run:
            log.info(f"DRY RUN: would apply to {job.get('apply_url')}")
            update_status(str(config.excel_tracker), jid, STATUS_PENDING, "dry_run — not applied")
            processed += 1
            continue

        # Step 7 — Apply
        log.info(f"Step 7: Applying to {job.get('apply_url')}")
        resume_to_upload = tailor_result if Path(tailor_result).exists() else str(config.base_resume)

        apply_result = run_application(
            job=job,
            resume_path=resume_to_upload,
            profile=profile,
            headless=config.headless,
            slow_mo=config.slow_mo,
            min_delay=config.min_delay,
            max_delay=config.max_delay,
        )

        reason = apply_result.get("reason", "")
        error  = apply_result.get("error") or ""

        if apply_result["success"]:
            status = STATUS_APPLIED
            notes  = f"method={apply_result.get('method', '')}"
        elif reason in ("captcha_detected",) or "captcha" in error.lower():
            status = STATUS_MANUAL
            notes  = "CAPTCHA detected — please apply manually via apply_url"
        elif reason == "2fa_required":
            status = STATUS_MANUAL
            notes  = "LinkedIn 2FA triggered — disable 2FA on your account or apply manually"
        elif reason == "linkedin_auth_required":
            status = STATUS_MANUAL
            notes  = "LinkedIn requires browser login — add LINKEDIN_EMAIL/PASSWORD to .env"
        elif "vision unavailable" in error.lower() or "manual_required" in error.lower():
            status = STATUS_MANUAL
            notes  = "No Easy Apply + vision form fill unavailable — apply manually via apply_url"
        elif reason == "no_easy_apply":
            status = STATUS_FAILED
            notes  = "No Easy Apply button and external form also failed"
        else:
            status = STATUS_FAILED
            notes  = error or reason or "Unknown failure"

        update_status(str(config.excel_tracker), jid, status, notes)
        log.info(f"Result: {status} | {notes}")

        processed += 1

        # Polite delay between applications (30–60 seconds)
        if processed < config.max_jobs_per_run and not dry_run:
            delay = random.uniform(30, 60)
            log.info(f"Waiting {delay:.0f}s before next application")
            time.sleep(delay)

    log.info(f"{'=' * 60}")
    log.info(f"Pipeline complete — {processed} jobs processed")
    log.info(f"{'=' * 60}")
    mark_ran_today(config.logs_dir)


# ------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Automated job application pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Use config.yaml defaults
  python main_pipeline.py

  # Dry run — no applying
  python main_pipeline.py --dry-run

  # Override role and location
  python main_pipeline.py --role "DevOps Engineer" --location Dubai "Abu Dhabi"

  # Full override
  python main_pipeline.py --role "SRE" --location Dubai --days 3 \\
      --platform linkedin --max-jobs 5 --min-score 65 \\
      --template modern --resume data/my_resume.pdf
        """,
    )
    parser.add_argument("--dry-run",   action="store_true",  help="Score and tailor but do not apply")
    parser.add_argument("--role",      type=str,             help="Job title / keyword to search")
    parser.add_argument("--location",  nargs="+",            help="One or more locations")
    parser.add_argument("--days",      type=int,             help="Only jobs posted in last N days")
    parser.add_argument("--platform",  nargs="+",            choices=["linkedin", "glassdoor", "naukri"],
                                                             help="Platforms to scrape")
    parser.add_argument("--max-jobs",  type=int, dest="max_jobs",   help="Max jobs to process per run")
    parser.add_argument("--min-score", type=int, dest="min_score",  help="Minimum match score (0–100)")
    parser.add_argument("--template",  choices=["professional", "modern", "classic"],
                                                             help="Resume PDF template")
    parser.add_argument("--resume",    type=str,             help="Path to base resume (PDF or DOCX)")

    args = parser.parse_args()
    overrides = {
        "role":      args.role,
        "location":  args.location,
        "days":      args.days,
        "platform":  args.platform,
        "max_jobs":  args.max_jobs,
        "min_score": args.min_score,
        "template":  args.template,
        "resume":    args.resume,
    }
    run_pipeline(dry_run=args.dry_run, overrides=overrides)
