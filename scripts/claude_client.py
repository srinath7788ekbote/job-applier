"""
claude_client.py
Unified LLM caller for the job-applier pipeline.

TEXT prompts (profile extraction, scoring, resume tailoring):
  1. claude CLI     — your Claude Code session, no key needed
  2. openclaw CLI   — your openclaw session, no key needed
  3. Anthropic SDK  — ANTHROPIC_API_KEY fallback

VISION prompts (external form screenshot analysis):
  1. Anthropic SDK    — ANTHROPIC_API_KEY
  2. Gemini API       — GEMINI_API_KEY
  3. GitHub Copilot   — GITHUB_TOKEN (OpenAI-compatible endpoint)
"""

import logging
import os
import subprocess
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-4-6"


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _clean_env() -> dict:
    """Strip CLAUDECODE so subprocesses can call the claude/openclaw CLI freely."""
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    return env


def _which(cmd: str) -> Optional[str]:
    import shutil
    return shutil.which(cmd) or shutil.which(f"{cmd}.cmd")


# ─────────────────────────────────────────────────────────────
# Text providers (no image support)
# ─────────────────────────────────────────────────────────────

def _call_cli(command: str, prompt: str, model: str) -> Optional[str]:
    """
    Generic CLI caller for claude or openclaw.
    Returns response text on success, None on any failure.
    """
    env = _clean_env()
    cmd_path = _which(command)
    if not cmd_path:
        return None

    for args in (
        [cmd_path, "-p", prompt, "--model", model],
        [cmd_path, "--print", "--model", model],   # stdin variant
    ):
        try:
            stdin_input = prompt if args[-1] == model and "--print" in args else None
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
            log.debug(f"{command} CLI error: {exc}")

    return None


def _call_via_claude_cli(prompt: str, model: str) -> Optional[str]:
    return _call_cli("claude", prompt, model)


def _call_via_openclaw_cli(prompt: str, model: str) -> Optional[str]:
    return _call_cli("openclaw", prompt, model)


def _call_via_anthropic_sdk(
    prompt: str,
    system: str,
    model: str,
    image_b64: Optional[str] = None,
) -> str:
    """Anthropic SDK — supports both text and vision. Requires ANTHROPIC_API_KEY."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    content: list = []
    if image_b64:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": image_b64},
        })
    content.append({"type": "text", "text": prompt})

    kwargs: dict = {
        "model": model,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": content}],
    }
    if system:
        kwargs["system"] = system

    msg = client.messages.create(**kwargs)
    return msg.content[0].text.strip()


def _call_via_gemini(
    prompt: str,
    system: str,
    image_b64: Optional[str] = None,
) -> str:
    """Gemini API vision + text. Requires GEMINI_API_KEY."""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")

    import google.generativeai as genai
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-pro")

    parts = []
    if image_b64:
        import base64
        parts.append({"mime_type": "image/png", "data": base64.b64decode(image_b64)})
    full_prompt = f"{system}\n\n{prompt}" if system else prompt
    parts.append(full_prompt)

    response = model.generate_content(parts)
    return response.text.strip()


def _call_via_github_copilot(
    prompt: str,
    system: str,
    image_b64: Optional[str] = None,
) -> str:
    """
    GitHub Copilot via its OpenAI-compatible endpoint.
    Requires GITHUB_TOKEN with an active Copilot subscription.
    Supports vision via gpt-4o.
    """
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError("GITHUB_TOKEN not set")

    import json
    import urllib.request

    content: list = []
    if image_b64:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{image_b64}"},
        })
    content.append({"type": "text", "text": prompt})

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": content})

    payload = json.dumps({
        "model": "gpt-4o",
        "messages": messages,
        "max_tokens": 1024,
    }).encode()

    req = urllib.request.Request(
        "https://api.githubcopilot.com/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Editor-Version": "vscode/1.85.0",
            "Copilot-Integration-Id": "vscode-chat",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode())
    return data["choices"][0]["message"]["content"].strip()


# ─────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────

def call_agent_browser(
    apply_url: str,
    resume_path: str,
    profile: dict,
) -> Optional[dict]:
    """
    Hand off external form filling to the ambient agent (claude CLI or openclaw).

    When the pipeline is running inside Claude Code or openclaw, the agent already
    has native browser tools (Playwright, WebFetch, Bash). We just describe the task
    and let the agent execute it — no extra API key or vision model needed.

    Returns a result dict {"success": bool, "method": "agent_browser", "error": str|None}
    or None if no agent CLI is available (caller should fall back to vision approach).
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

    env = _clean_env()

    for cli in ("claude", "openclaw"):
        cmd = _which(cli)
        if not cmd:
            continue
        try:
            log.info(f"Handing external form to {cli} agent browser")
            result = subprocess.run(
                [cmd, "-p", task, "--dangerously-skip-permissions"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,   # 5 min — agent needs time to browse + fill
                env=env,
            )
            output = (result.stdout or "").strip()
            log.debug(f"{cli} agent output:\n{output[-500:]}")

            # Extract the last JSON object from the output
            matches = re.findall(r'\{[^{}]*"success"[^{}]*\}', output)
            if matches:
                parsed = json.loads(matches[-1])
                log.info(f"Agent browser result: {parsed}")
                return parsed

            # No structured JSON — infer from text
            last_lines = output.lower().split("\n")[-5:]
            if any("success" in l and ("true" in l or ": true" in l) for l in last_lines):
                return {"success": True, "method": "agent_browser", "error": None}
            if result.returncode != 0:
                log.warning(f"{cli} agent exited {result.returncode}: {result.stderr[:300]}")
                continue
            return {"success": False, "method": "agent_browser",
                    "error": "Agent completed but returned no structured result"}

        except subprocess.TimeoutExpired:
            log.warning(f"{cli} agent timed out (5 min) filling form")
            return {"success": False, "method": "agent_browser", "error": "Agent timed out"}
        except Exception as exc:
            log.debug(f"{cli} agent error: {exc}")
            continue

    return None  # No agent CLI available — caller falls back to vision


def call_claude(
    prompt: str,
    system: str = "",
    model: str = DEFAULT_MODEL,
    image_b64: Optional[str] = None,
) -> str:
    """
    Call the best available LLM and return the response text.

    TEXT (no image):
      claude CLI → openclaw CLI → Anthropic SDK

    VISION (image_b64 provided):
      Anthropic SDK → Gemini API → GitHub Copilot
    """
    if image_b64:
        # Vision chain
        errors = []
        for name, fn in [
            ("Anthropic SDK", lambda: _call_via_anthropic_sdk(prompt, system, model, image_b64)),
            ("Gemini API",    lambda: _call_via_gemini(prompt, system, image_b64)),
            ("GitHub Copilot", lambda: _call_via_github_copilot(prompt, system, image_b64)),
        ]:
            try:
                result = fn()
                log.debug(f"Vision call handled by {name}")
                return result
            except Exception as exc:
                log.debug(f"{name} unavailable: {exc}")
                errors.append(f"{name}: {exc}")

        raise RuntimeError(
            "No vision-capable LLM is configured.\n"
            "Set one of the following in your .env file:\n"
            "  ANTHROPIC_API_KEY=sk-ant-...\n"
            "  GEMINI_API_KEY=AIza...\n"
            "  GITHUB_TOKEN=ghp_...  (needs Copilot subscription)\n"
            f"Attempted providers:\n" + "\n".join(f"  - {e}" for e in errors)
        )

    # Text-only chain
    full_prompt = f"{system}\n\n{prompt}" if system else prompt

    response = _call_via_claude_cli(full_prompt, model)
    if response:
        log.debug("Text call handled by claude CLI")
        return response

    response = _call_via_openclaw_cli(full_prompt, model)
    if response:
        log.debug("Text call handled by openclaw CLI")
        return response

    log.info("Neither claude nor openclaw CLI available — falling back to Anthropic SDK")
    return _call_via_anthropic_sdk(prompt, system, model)


def strip_json_fences(text: str) -> str:
    """Remove markdown code fences that LLMs sometimes wrap around JSON."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0].strip()
    return text
