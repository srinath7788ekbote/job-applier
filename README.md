# Job Applier — Automated Job Application Pipeline

Fully automated end-to-end pipeline that scrapes job listings, scores them against your resume, tailors a PDF resume for each qualifying role, and applies on your behalf using browser automation.

---

## What It Does (End to End)

```
1. Extract Profile    — parse your resume once and cache your profile (name, email, skills, etc.)
2. Scrape Jobs        — search LinkedIn / Glassdoor / Naukri for your target role and location
3. Filter Seen Jobs   — skip any job already in the Excel tracker
4. Score Each Job     — Claude compares the JD against your resume and gives a 0–100 match score
5. Skip Low Scores    — jobs below min_match_score are logged as "skipped" and not applied to
6. Tailor Resume      — Claude rewrites your resume to highlight keywords from the JD, renders to PDF
7. Log to Tracker     — job added to jobs_tracker.xlsx with score, gaps, and resume path
8. Apply              — browser automation fills and submits the application:
                          a) LinkedIn Easy Apply (multi-step modal, file upload)
                          b) External form — agent browser (Claude Code / openclaw playwright-cli)
                          c) Vision-guided form fill (Anthropic SDK / Gemini / GitHub Copilot)
                          d) Blind fill fallback (heuristic label matching)
9. Update Status      — tracker updated: applied / failed / manual_required / skipped
10. Run Guard         — writes today's date to logs/ran_dates.txt so it only runs once per day
```

**Hard rules enforced on every tailored resume:** job titles, employer names, dates, education, and personal contact info are never changed — only keywords and phrasing are adjusted to match the JD.

---

## File Structure

```
job-applier/
├── main_pipeline.py          # Orchestrator — runs all 10 steps above
├── config.yaml               # All user-facing settings (role, location, thresholds, template)
├── requirements.txt          # Python dependencies
├── test_setup.py             # Sanity-check script — run before first use
├── .env                      # Secrets — LinkedIn credentials, optional API keys (gitignored)
├── .env.example              # Template for .env
│
├── scripts/
│   ├── claude_client.py      # Unified LLM caller (claude CLI → openclaw → SDK fallback)
│   ├── scraper_wrapper.py    # Calls vendor/job-scraper as subprocess, normalises output
│   ├── extract_profile.py    # Parses resume → profile dict, caches to data/profile_cache.json
│   ├── compare_resume.py     # Scores job JD vs resume (0–100), returns gaps & keywords
│   ├── resume_wrapper.py     # Calls Claude to tailor JSON, then renders PDF via resume-skill
│   ├── apply_jobs.py         # Playwright browser automation (Easy Apply + external forms)
│   └── update_excel.py       # Excel tracker read/write with file-lock safety
│
├── data/
│   ├── base_resume.pdf       # YOUR RESUME — drop it here (PDF or DOCX)
│   ├── jobs_tracker.xlsx     # Auto-generated Excel log of every job processed
│   ├── tailored/             # Auto-generated tailored resume PDFs (gitignored)
│   ├── profile_cache.json    # Cached profile extracted from your resume (gitignored)
│   └── linkedin_session.json # Saved LinkedIn cookies for session reuse (gitignored)
│
├── logs/
│   ├── pipeline_YYYY-MM-DD.log  # Daily run log
│   └── ran_dates.txt            # Run-guard: prevents duplicate runs on same day
│
├── scheduler/
│   ├── run_pipeline.sh          # Cron job script (Linux / macOS)
│   └── setup_windows_task.ps1   # Windows Task Scheduler setup
│
└── vendor/
    ├── job-scraper/          # Job scraping repo (see below)
    └── resume-skill/         # Resume tailoring + PDF rendering repo (see below)
```

---

## Vendor Repos

### 1. `vendor/job-scraper` — Job Scraper

Scrapes job listings from multiple platforms using headless browsers (Playwright/Selenium). Called as a subprocess by `scripts/scraper_wrapper.py`.

**Supported platforms:**

| Platform | Key | Notes |
|----------|-----|-------|
| LinkedIn | `linkedin` | Best coverage, most fields |
| Glassdoor | `glassdoor` | Includes salary ranges |
| Naukri | `naukri` | India-focused |

Configure which platforms to use in `config.yaml` under `target_platforms`.

---

### 2. `vendor/resume-skill` — Resume Builder

Two-stage pipeline: Claude tailors the resume content as JSON → Playwright renders it to a PDF using an HTML template.

#### Resume Templates

There are **3 templates** to choose from:

| Template | Style | Best For |
|----------|-------|----------|
| `professional` | Centered bold name, single contact line, clean black/white | **Default.** Traditional roles, finance, consulting |
| `modern` | Left-aligned name, teal accent strip, tech-forward layout | Startups, product, software engineering |
| `classic` | Dark navy header band, white name, executive feel | Senior / executive roles |

**Template files:** `vendor/resume-skill/templates/{professional,modern,classic}.py`

**To change the template,** edit one line in `config.yaml`:
```yaml
pipeline:
  resume_template: "modern"   # professional | modern | classic
```

---

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.10+ | [python.org](https://python.org) |
| Node.js | 18+ | Required by Playwright browsers — [nodejs.org](https://nodejs.org) |
| Git | any | For cloning repos |
| make | any | `winget install GnuWin32.Make` (Windows) · pre-installed on macOS/Linux |
| Claude Code CLI | latest | `npm install -g @anthropic/claude-code` — primary LLM, **no API key needed** |
| **OR** openclaw | latest | Alternative ambient AI — works the same way as Claude Code |

> The pipeline calls Claude for all AI tasks. If you run it from inside a **Claude Code** or **openclaw** session, no API key is required. API keys are only needed as fallbacks.

**Optional API keys (for external form vision fallback):**

| Variable | Provider | Notes |
|----------|----------|-------|
| `ANTHROPIC_API_KEY` | Anthropic | Best quality; also enables text fallback without CLI |
| `GEMINI_API_KEY` | Google Gemini | Free tier available |
| `GITHUB_TOKEN` | GitHub Copilot | Requires active Copilot subscription |

---

## Setup

### Step 1 — Clone the repo (with submodules)

`vendor/job-scraper` and `vendor/resume-skill` are git submodules — they are pulled automatically:

```bash
git clone --recurse-submodules https://github.com/srinath7788ekbote/job-applier.git
cd job-applier
```

If you already cloned without `--recurse-submodules`, run:
```bash
git submodule update --init --recursive
```

---

### Step 2 — Create virtual environment and install dependencies

```bash
cd job-applier

# Create venv
python -m venv venv

# Activate
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt

# Install Playwright browser (Chromium)
playwright install chromium
```

---

### Step 3 — Add your resume

Drop your resume (PDF or DOCX) into the `data/` folder and name it:

```
data/base_resume.pdf
```

---

### Step 4 — Configure `.env`

```bash
cp .env.example .env
```

Edit `.env` with your LinkedIn credentials:

```env
# Required — LinkedIn login for automated applying
LINKEDIN_EMAIL=your@email.com
LINKEDIN_PASSWORD=yourpassword

# Optional — uncomment whichever you have for external form vision
# ANTHROPIC_API_KEY=sk-ant-...
# GEMINI_API_KEY=AIza...
# GITHUB_TOKEN=ghp_...
```

---

### Step 5 — Configure `config.yaml`

```yaml
pipeline:
  target_role: "Site Reliability Engineer"   # job title to search
  target_location:
    - "Dubai"                                 # one or more locations
    - "Abu Dhabi"
  target_platforms:
    - "linkedin"                              # linkedin | glassdoor | naukri
  max_jobs_per_run: 5                         # max jobs processed per daily run
  days_back: 1                                # only jobs posted in last N days
  min_match_score: 65                         # skip jobs scoring below this (0–100)
  resume_template: "professional"             # professional | modern | classic
```

---

### Step 6 — Verify setup

```bash
python -X utf8 test_setup.py
```

Checks: Python version, claude CLI, all dependencies, vendor repos, config file, resume file, profile extraction, job scoring, Excel tracker, and Playwright.

---

## Running

All commands go through `make`. Every run target accepts optional override variables.

### Common commands

```bash
make run                          # Full run using config.yaml
make dry                          # Dry run — score + tailor, no applying
make test                         # Sanity check (dependencies, config, Playwright)
make clean                        # Delete cache, logs, tailored PDFs, tracker
make reset                        # clean + delete LinkedIn session (full fresh start)
make force                        # Clear run-guard to run again today
```

### Override variables inline

Any `make run` / `make dry` / `make search` accepts these variables:

| Variable | Description | Example |
|----------|-------------|---------|
| `ROLE` | Job title to search | `ROLE="DevOps Engineer"` |
| `LOCATION` | One or more locations | `LOCATION="Dubai"` or `LOCATION="Dubai" LOCATION="Abu Dhabi"` |
| `DAYS` | Jobs posted in last N days | `DAYS=3` |
| `PLATFORM` | `linkedin` / `glassdoor` / `naukri` | `PLATFORM=linkedin` |
| `MAX_JOBS` | Max jobs per run | `MAX_JOBS=5` |
| `MIN_SCORE` | Minimum match score (0–100) | `MIN_SCORE=70` |
| `TEMPLATE` | Resume PDF template | `TEMPLATE=modern` |
| `RESUME` | Path to base resume | `RESUME=data/cv.pdf` |

### Examples

```bash
# Run with defaults from config.yaml
make run

# Dry run — preview matches without applying
make dry ROLE="SRE" LOCATION="Abu Dhabi" DAYS=7

# Full run overriding everything
make search ROLE="Cloud Engineer" LOCATION=Dubai DAYS=1 \
            PLATFORM=linkedin MAX_JOBS=5 TEMPLATE=modern

# Use a different resume file
make run RESUME=data/senior_cv.pdf MIN_SCORE=75

# Force re-run same day after clearing run-guard
make force && make run
```

### Without make (direct Python)

```bash
python -X utf8 main_pipeline.py
python -X utf8 main_pipeline.py --dry-run
python -X utf8 main_pipeline.py --role "SRE" --location "Abu Dhabi" --days 7
python -X utf8 main_pipeline.py --help    # full list of arguments
```

---

## Scheduling (optional — run daily automatically)

### Windows Task Scheduler
```powershell
powershell -ExecutionPolicy Bypass -File scheduler\setup_windows_task.ps1
```

### Linux / macOS — cron (daily at 9 AM)
```bash
crontab -e
# Add this line:
0 9 * * * /path/to/job-applier/scheduler/run_pipeline.sh
```

---

## Output Files

| File | Description |
|------|-------------|
| `data/jobs_tracker.xlsx` | All processed jobs with score, status, gaps, and resume path |
| `data/tailored/First_Last_Company.pdf` | Tailored resume PDF per qualifying job |
| `logs/pipeline_YYYY-MM-DD.log` | Full run log |

### Job Status Codes in Tracker

| Status | Colour | Meaning |
|--------|--------|---------|
| `applied` | Green | Successfully submitted |
| `manual_required` | Orange | CAPTCHA / 2FA / no vision key — open `apply_url` and apply manually |
| `failed` | Red | Apply attempted but submission could not be confirmed |
| `skipped` | Grey | Score below `min_match_score` — not worth applying |
| `pending` | Yellow | Dry-run entry, or apply not yet attempted |

---

## How Claude Is Used

The pipeline uses Claude for three tasks. Providers are tried in order — **no API key needed when running inside Claude Code or openclaw:**

| Task | Provider order |
|------|---------------|
| Profile extraction, scoring, resume tailoring | claude CLI → openclaw CLI → Anthropic SDK |
| External form browser automation | claude CLI (`playwright-cli` skill) → openclaw → vision fallback |
| External form screenshot analysis (vision) | Anthropic SDK → Gemini API → GitHub Copilot |

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `Resume not found` | Place your resume at `data/base_resume.pdf` |
| `No jobs scraped` | Try increasing `days_back` or check your location spelling in `config.yaml` |
| `Score always 0` | Claude CLI not found — run `claude --version` to check |
| LinkedIn auth wall | Ensure `LINKEDIN_EMAIL` / `LINKEDIN_PASSWORD` are set in `.env` |
| LinkedIn 2FA triggered | Disable 2FA on your LinkedIn account, then delete `data/linkedin_session.json` and re-run |
| External form → `manual_required` | Run from inside Claude Code (uses playwright-cli skill automatically), or add an API key to `.env` |
| Excel file locked | Close the file in Excel and re-run; the fallback file is saved as `data/jobs_tracker_HHMMSS.xlsx` |
| Windows encoding errors | Always run with `python -X utf8 main_pipeline.py` |
