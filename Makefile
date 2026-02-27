# =============================================================================
# Job Applier — Makefile
# =============================================================================
# Works on Windows (Git Bash / WSL), macOS, and Linux.
#
# Requires `make`. Install if needed:
#   Windows : winget install GnuWin32.Make  OR  choco install make  OR  scoop install make
#             Then run from Git Bash (not CMD/PowerShell)
#   macOS   : pre-installed  (or: xcode-select --install)
#   Linux   : sudo apt install make   /   sudo yum install make
# =============================================================================
# Usage:
#   make <target> [VAR=value ...]
#
# Quick examples:
#   make setup
#   make run
#   make dry  ROLE="DevOps Engineer" LOCATION="Dubai" DAYS=3
#   make search ROLE="SRE" LOCATION="Abu Dhabi" PLATFORM=linkedin DAYS=7
#   make clean
# =============================================================================

# ── Python interpreter ────────────────────────────────────────────────────────
# Auto-detects venv (Windows path first, then Unix), falls back to system python.
VENV_WIN  := venv/Scripts/python.exe
VENV_UNIX := venv/bin/python
PYTHON    := $(shell \
  if [ -f "$(VENV_WIN)" ];  then echo "$(VENV_WIN)"; \
  elif [ -f "$(VENV_UNIX)" ]; then echo "$(VENV_UNIX)"; \
  else echo "python"; fi)
RUN       := $(PYTHON) -X utf8

# Python to use for `make setup` (before venv exists).
# Tries python3 first (Linux/macOS), falls back to python (Windows).
SETUP_PYTHON := $(shell command -v python3 2>/dev/null || command -v python 2>/dev/null || echo python)

# ── Override variables (pass on command line) ─────────────────────────────────
ROLE      ?=
LOCATION  ?=
DAYS      ?=
PLATFORM  ?=
MAX_JOBS  ?=
MIN_SCORE ?=
TEMPLATE  ?=
RESUME    ?=

# Build optional args string from whatever variables are set
ARGS :=
ifneq ($(ROLE),)
  ARGS += --role "$(ROLE)"
endif
ifneq ($(LOCATION),)
  ARGS += --location $(LOCATION)
endif
ifneq ($(DAYS),)
  ARGS += --days $(DAYS)
endif
ifneq ($(PLATFORM),)
  ARGS += --platform $(PLATFORM)
endif
ifneq ($(MAX_JOBS),)
  ARGS += --max-jobs $(MAX_JOBS)
endif
ifneq ($(MIN_SCORE),)
  ARGS += --min-score $(MIN_SCORE)
endif
ifneq ($(TEMPLATE),)
  ARGS += --template $(TEMPLATE)
endif
ifneq ($(RESUME),)
  ARGS += --resume "$(RESUME)"
endif

# =============================================================================
# Targets
# =============================================================================

.PHONY: help setup install browsers test run dry search clean reset force

help: ## Show this help
	@echo ""
	@echo "  Job Applier — available commands"
	@echo ""
	@echo "  SETUP"
	@echo "    make setup          Create venv, install deps, install Playwright browser"
	@echo "    make install        Re-install Python dependencies only"
	@echo "    make browsers       Re-install Playwright browser (Chromium)"
	@echo "    make test           Run sanity checks (test_setup.py)"
	@echo ""
	@echo "  RUNNING"
	@echo "    make run            Full run using config.yaml"
	@echo "    make dry            Dry run — score + tailor, NO applying"
	@echo "    make search         Run with inline variable overrides (see below)"
	@echo ""
	@echo "  MAINTENANCE"
	@echo "    make clean          Delete cache, logs, tailored PDFs, tracker"
	@echo "    make reset          Clean + delete LinkedIn session (full fresh start)"
	@echo "    make force          Clear run-guard so pipeline can run again today"
	@echo ""
	@echo "  OVERRIDE VARIABLES (append to any run/dry/search target)"
	@echo "    ROLE      Job title to search          e.g.  ROLE=\"DevOps Engineer\""
	@echo "    LOCATION  One or more locations        e.g.  LOCATION=\"Dubai\""
	@echo "    DAYS      Posted in last N days        e.g.  DAYS=3"
	@echo "    PLATFORM  linkedin|glassdoor|naukri    e.g.  PLATFORM=linkedin"
	@echo "    MAX_JOBS  Max jobs per run             e.g.  MAX_JOBS=5"
	@echo "    MIN_SCORE Min match score (0-100)      e.g.  MIN_SCORE=70"
	@echo "    TEMPLATE  professional|modern|classic  e.g.  TEMPLATE=modern"
	@echo "    RESUME    Path to base resume          e.g.  RESUME=data/cv.pdf"
	@echo ""
	@echo "  EXAMPLES"
	@echo "    make run"
	@echo "    make dry ROLE=\"SRE\" LOCATION=\"Abu Dhabi\" DAYS=7"
	@echo "    make search ROLE=\"Cloud Engineer\" LOCATION=Dubai DAYS=1 PLATFORM=linkedin"
	@echo "    make search ROLE=\"DevOps\" LOCATION=\"Abu Dhabi\" TEMPLATE=modern MAX_JOBS=3"
	@echo "    make run RESUME=data/senior_cv.pdf MIN_SCORE=70"
	@echo ""

# ── Setup ─────────────────────────────────────────────────────────────────────

setup: ## Create venv, install all deps, install Playwright Chromium
	git submodule update --init --recursive
	$(SETUP_PYTHON) -m venv venv
	$(RUN) -m pip install --upgrade pip
	$(RUN) -m pip install -r requirements.txt
	$(PYTHON) -m playwright install chromium
	@mkdir -p data/tailored logs
	@echo ""
	@echo "  Setup complete. Next steps:"
	@echo "    1. Drop your resume at:   data/base_resume.pdf"
	@echo "    2. Copy and edit .env:    cp .env.example .env"
	@echo "    3. Edit config.yaml with your target role and location"
	@echo "    4. Run: make test"
	@echo ""

install: ## Re-install Python dependencies
	$(RUN) -m pip install -r requirements.txt

browsers: ## Re-install Playwright Chromium browser
	$(PYTHON) -m playwright install chromium

test: ## Run sanity checks
	$(RUN) test_setup.py

# ── Running ───────────────────────────────────────────────────────────────────

run: ## Full run (scrape → score → tailor → apply) using config.yaml
	$(RUN) main_pipeline.py $(ARGS)

dry: ## Dry run — scrape + score + tailor, no applying
	$(RUN) main_pipeline.py --dry-run $(ARGS)

search: ## Run with inline overrides (use VAR=value on command line)
	$(RUN) main_pipeline.py $(ARGS)

# ── Maintenance ───────────────────────────────────────────────────────────────

clean: ## Delete cache, logs, tailored PDFs, tracker, screenshots
	@echo "Cleaning generated files..."
	-rm -f data/profile_cache.json data/profile_cache.meta.json
	-rm -f data/jobs_tracker.xlsx data/jobs_tracker_*.xlsx
	-rm -f data/tailored/*.pdf
	-rm -f logs/pipeline_*.log logs/ran_dates.txt
	-rm -rf .playwright-cli
	-rm -f *.png
	@mkdir -p data/tailored logs
	@echo "Done."

reset: clean ## Full fresh start (clean + delete LinkedIn session)
	-rm -f data/linkedin_session.json
	@echo "LinkedIn session cleared. Next run will log in fresh."

force: ## Clear run-guard so pipeline can run again today
	@mkdir -p logs
	@printf '' > logs/ran_dates.txt
	@echo "Run-guard cleared — pipeline will run again today."
