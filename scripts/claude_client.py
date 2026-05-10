"""
claude_client.py
LLM caller for the job-applier pipeline.

Uses claude CLI exclusively for all AI tasks.
No API keys required — runs inside a Claude Code session.
"""

import json
import logging
import os
import subprocess
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-6"


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def _clean_env() -> dict:
    """Strip CLAUDECODE so subprocesses can call the claude CLI freely."""
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    return env


def _which(cmd: str) -> Optional[str]:
    import shutil
    return shutil.which(cmd) or shutil.which(f"{cmd}.cmd")


# ─────────────────────────────────────────────────────────
# Provider
# ─────────────────────────────────────────────────────────

def _run_cli(args: list[str], prompt: str, stdin_input: str | None = None) -> Optional[str]:
    """Run a CLI command and return stdout on success, None on failure."""
    env = _clean_env()
    try:
        result = subprocess.run(
            args,
            input=stdin_input,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            env=env,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    except Exception as exc:
        log.debug(f"{args[0]} CLI error: {exc}")
    return None


def _call_via_claude_cli(prompt: str, model: str) -> Optional[str]:
    """Claude CLI: `claude -p "prompt" --model <model>`"""
    cmd = _which("claude")
    if not cmd:
        return None
    return (
        _run_cli([cmd, "-p", prompt, "--model", model], prompt)
        or _run_cli([cmd, "--print", "--model", model], prompt, stdin_input=prompt)
    )


# ─────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────

def call_agent_browser(
    apply_url: str,
    resume_path: str,
    profile: dict,
) -> Optional[dict]:
    """
    Hand off external form filling to the claude CLI agent.

    When the pipeline is running inside Claude Code, the agent already
    has native browser tools (Playwright, WebFetch, Bash). We just describe the task
    and let the agent execute it.

    Returns a result dict {"success": bool, "method": "agent_browser", "error": str|None}
    or None if claude CLI is not available.
    """
    import re

    # Build a clean profile block (only non-empty fields)
    profile_lines = []
    field_labels = [
        ("full_name",          "Full Name"),
        ("email",              "Email"),
        ("phone",              "Phone"),
        ("linkedin_url",       "LinkedIn URL"),
        ("github_url",         "GitHub URL"),
        ("current_title",      "Current Title"),
        ("location",           "Location"),
        ("work_authorization", "Work Authorization"),
        ("years_of_experience","Years of Experience"),
    ]
    for key, label in field_labels:
        val = profile.get(key)
        if val:
            profile_lines.append(f"  {label}: {val}")
    profile_block = "\n".join(profile_lines)

    task = f"""You are acting as a job application agent. Your task is to fill and submit an online job application form using the playwright-cli skill.

=== TASK ===
Navigate to the URL below and complete the application form on behalf of the applicant.

URL: {apply_url}

=== APPLICANT PROFILE ===
{profile_block}

=== RESUME FILE ===
Upload this file when a CV / resume file-upload field is present:
  {resume_path}

=== INSTRUCTIONS ===
Use the playwright-cli skill to complete this task:

1. Run: /playwright-cli
2. Open the URL:          playwright-cli open {apply_url}
3. Take a snapshot:       playwright-cli snapshot
4. Accept any cookie banners (click Accept button if present)
5. Click the Apply / Apply now button to open the application form
6. Take a snapshot to see the form fields
7. Fill each visible field using `playwright-cli fill <ref> "<value>"`
   - First name / Given name → {profile.get("full_name", "").split()[0] if profile.get("full_name") else ""}
   - Last name / Family name → {profile.get("full_name", "").split()[-1] if profile.get("full_name") else ""}
   - Full name              → {profile.get("full_name", "")}
   - Email                  → {profile.get("email", "")}
   - Phone                  → {profile.get("phone", "")}
   - City / Location        → {profile.get("location", "").split(",")[0].strip() if profile.get("location") else ""}
   - Country                → {profile.get("location", "").split(",")[-1].strip() if profile.get("location") else ""}
   - Address                → {profile.get("location", "")}
8. If a file upload field exists: click it, then run:
   playwright-cli upload "{resume_path}"
9. Click the Submit / Apply / Send button
10. Take a final snapshot to confirm submission
11. Close browser: playwright-cli close

=== OUTPUT ===
After completing (or failing), return ONLY this JSON on the last line:
{{"success": true, "method": "agent_browser", "error": null}}
or if it failed:
{{"success": false, "method": "agent_browser", "error": "brief reason"}}"""

    cmd = _which("claude")
    if not cmd:
        return None

    env = _clean_env()
    try:
        log.info("Handing external form to claude CLI agent browser")
        result = subprocess.run(
            [cmd, "-p", task],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,   # 5 min — agent needs time to browse + fill
            env=env,
        )
        output = (result.stdout or "").strip()
        log.debug(f"claude agent output:\n{output[-500:]}")

        # Extract the last JSON object from the output
        matches = re.findall(r'\{[^{}]*"success"[^{}]*\}', output)
        if matches:
            parsed = json.loads(matches[-1])
            log.info(f"Agent browser result (claude): {parsed}")
            return parsed

        # No structured JSON — infer from text
        last_lines = output.lower().split("\n")[-5:]
        if any("success" in l and ("true" in l or ": true" in l) for l in last_lines):
            return {"success": True, "method": "agent_browser", "error": None}
        if result.returncode != 0:
            log.warning(f"claude agent exited {result.returncode}: {result.stderr[:300]}")
            return None

        log.warning("claude completed (exit 0) but returned no structured result")
        return None

    except subprocess.TimeoutExpired:
        log.warning("claude agent timed out (5 min)")
        return None
    except Exception as exc:
        log.debug(f"claude agent error: {exc}")
        return None


def call_llm(
    prompt: str,
    system: str = "",
    model: str = DEFAULT_MODEL,
    image_b64: Optional[str] = None,
) -> str:
    """
    Call Claude CLI and return the response text.

    Vision (image_b64) is not supported in CLI-only mode — raises RuntimeError
    so callers can fall back to alternative strategies (e.g. blind form fill).
    """
    if image_b64:
        raise RuntimeError(
            "Vision calls not supported in CLI-only mode. "
            "External form fill will use blind fill instead."
        )

    full_prompt = f"{system}\n\n{prompt}" if system else prompt

    response = _call_via_claude_cli(full_prompt, model)
    if response:
        log.debug("Text call handled by claude CLI")
        return response

    raise RuntimeError(
        "claude CLI is not available. Install Claude Code: "
        "npm install -g @anthropic-ai/claude-code"
    )


call_claude = call_llm  # backward compat alias


def strip_json_fences(text: str) -> str:
    """Remove markdown code fences that LLMs sometimes wrap around JSON."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0].strip()
    return text
