"""
apply_jobs.py
Browser automation for job applications using Playwright (sync API).

Supports:
  - LinkedIn Easy Apply (multi-step modal)
  - External application forms (Claude Vision-guided)

CAPTCHA detection: if detected in headless mode, marks job as manual_required
so the user can apply manually later.
"""

import base64
import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Optional

from claude_client import call_agent_browser, call_claude, strip_json_fences
from playwright.sync_api import sync_playwright, Page, BrowserContext, Locator

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
LINKEDIN_SESSION_FILE = BASE_DIR / "data" / "linkedin_session.json"

STEALTH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = { runtime: {} };
Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
"""

# Text patterns that indicate a CAPTCHA challenge
CAPTCHA_PATTERNS = [
    "verify you are human",
    "captcha",
    "i'm not a robot",
    "security check",
    "prove you're human",
    "are you a robot",
    "human verification",
]

# LinkedIn sign-in wall indicators (requires browser auth to proceed)
LINKEDIN_AUTH_WALL_SELECTORS = [
    ".modal__overlay--visible",           # sign-in overlay blocking the page
    '[data-tracking-control-name="csm-v2_sign-in-session-key"]',  # sign-in field
    'form[action*="login"]',
    'a[href*="linkedin.com/login"]',
]


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def human_delay(min_s: float = 1.5, max_s: float = 4.0) -> None:
    time.sleep(random.uniform(min_s, max_s))


def human_click(page: Page, locator: Locator, min_s: float = 0.5, max_s: float = 1.5) -> None:
    """Move mouse to element with random offset, then click."""
    try:
        box = locator.bounding_box()
        if box:
            x = box["x"] + random.uniform(2, box["width"] - 2)
            y = box["y"] + random.uniform(2, box["height"] - 2)
            page.mouse.move(x, y)
            human_delay(0.1, 0.3)
        locator.click()
        human_delay(min_s, max_s)
    except Exception as exc:
        log.warning(f"human_click fallback: {exc}")
        locator.click()


def find_by_label(page: Page, label_text: str) -> Optional[Locator]:
    """
    Try multiple strategies to find an input for the given label text.
    Returns a Locator or None.
    """
    text_lower = label_text.lower()
    strategies = [
        # By associated label element text
        f'//label[contains(translate(text(),"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"),"{text_lower}")]',
        # By placeholder
        f'input[placeholder*="{label_text}" i], textarea[placeholder*="{label_text}" i]',
        # By aria-label
        f'input[aria-label*="{label_text}" i], textarea[aria-label*="{label_text}" i], select[aria-label*="{label_text}" i]',
        # By name attribute
        f'input[name*="{text_lower}"], select[name*="{text_lower}"], textarea[name*="{text_lower}"]',
        # By id
        f'input[id*="{text_lower}"], select[id*="{text_lower}"], textarea[id*="{text_lower}"]',
    ]

    for strategy in strategies:
        try:
            if strategy.startswith("//"):
                labels = page.locator(f"xpath={strategy}")
                if labels.count() > 0:
                    label_el = labels.first
                    for_attr = label_el.get_attribute("for")
                    if for_attr:
                        input_loc = page.locator(f"#{for_attr}")
                        if input_loc.count() > 0:
                            return input_loc.first
            else:
                loc = page.locator(strategy)
                if loc.count() > 0:
                    return loc.first
        except Exception:
            continue
    return None


def _detect_captcha(page: Page) -> bool:
    """Return True if the page contains CAPTCHA indicators."""
    try:
        text = page.inner_text("body").lower()
        return any(pattern in text for pattern in CAPTCHA_PATTERNS)
    except Exception:
        return False


def _detect_linkedin_auth_wall(page: Page) -> bool:
    """Return True if LinkedIn is showing a sign-in wall blocking the apply flow."""
    for selector in LINKEDIN_AUTH_WALL_SELECTORS:
        try:
            if page.locator(selector).count() > 0:
                return True
        except Exception:
            continue
    return False


def _fill_field(page: Page, label: str, value: str) -> bool:
    """Find field by label and fill it. Returns True on success."""
    if not value:
        return False
    loc = find_by_label(page, label)
    if loc is None:
        return False
    try:
        tag = loc.evaluate("el => el.tagName.toLowerCase()")
        if tag == "select":
            # Try exact match, then closest option
            options = loc.evaluate("el => Array.from(el.options).map(o => o.text)")
            match = next((o for o in options if value.lower() in o.lower()), None)
            if match:
                loc.select_option(label=match)
        else:
            loc.fill(str(value))
        human_delay(0.3, 0.8)
        return True
    except Exception as exc:
        log.warning(f"Could not fill '{label}': {exc}")
        return False


def _fill_form_from_profile(page: Page, profile: dict) -> None:
    """Fill all visible form fields using profile data."""
    field_map = {
        "first name":       profile.get("full_name", "").split()[0] if profile.get("full_name") else "",
        "last name":        profile.get("full_name", "").split()[-1] if profile.get("full_name") else "",
        "full name":        profile.get("full_name", ""),
        "name":             profile.get("full_name", ""),
        "email":            profile.get("email", ""),
        "phone":            profile.get("phone", ""),
        "linkedin":         profile.get("linkedin_url", ""),
        "github":           profile.get("github_url", ""),
        "portfolio":        profile.get("portfolio_url", ""),
        "location":         profile.get("location", ""),
        "city":             profile.get("location", ""),
        "work authorization": profile.get("work_authorization", ""),
        "authorized":       profile.get("work_authorization", ""),
        "years of experience": str(profile.get("years_of_experience", "")),
        "experience":       str(profile.get("years_of_experience", "")),
    }
    for label, value in field_map.items():
        if value:
            _fill_field(page, label, value)


# ---------------------------------------------------------------------------
# LinkedIn Easy Apply
# ---------------------------------------------------------------------------

def apply_linkedin_easy_apply(
    page: Page,
    job_url: str,
    resume_path: str,
    profile: dict,
    min_delay: float = 1.5,
    max_delay: float = 4.0,
) -> dict:
    """
    Attempt LinkedIn Easy Apply.
    Returns dict: {"success": bool, "method": "easy_apply", "reason": str|None, "error": str|None}
    """
    log.info(f"Navigating to LinkedIn job: {job_url}")
    page.goto(job_url, wait_until="domcontentloaded", timeout=60000)
    human_delay(min_delay, max_delay)

    if _detect_captcha(page):
        return {"success": False, "method": "easy_apply", "reason": "captcha_detected",
                "error": "CAPTCHA detected — apply manually"}

    if _detect_linkedin_auth_wall(page):
        return {"success": False, "method": "easy_apply", "reason": "linkedin_auth_required",
                "error": "LinkedIn sign-in wall — browser session not authenticated"}

    # Find Easy Apply button
    easy_apply_btn = None
    for selector in [
        'button:has-text("Easy Apply")',
        'button[aria-label*="Easy Apply"]',
        '.jobs-apply-button',
    ]:
        loc = page.locator(selector)
        if loc.count() > 0:
            easy_apply_btn = loc.first
            break

    if easy_apply_btn is None:
        return {"success": False, "method": "easy_apply", "reason": "no_easy_apply", "error": None}

    log.info("Found Easy Apply button — clicking")
    human_click(page, easy_apply_btn)
    human_delay(min_delay, max_delay)

    # Multi-step modal loop (max 10 steps to prevent infinite loops)
    for step in range(10):
        if _detect_captcha(page):
            return {"success": False, "method": "easy_apply", "reason": "captcha_detected",
                    "error": "CAPTCHA appeared during Easy Apply"}

        # Upload resume if file input present
        file_inputs = page.locator('input[type="file"]')
        if file_inputs.count() > 0:
            try:
                file_inputs.first.set_input_files(resume_path)
                log.info("Resume uploaded in Easy Apply modal")
                human_delay(1.0, 2.0)
            except Exception as exc:
                log.warning(f"File upload failed: {exc}")

        # Fill visible form fields
        _fill_form_from_profile(page, profile)

        # Detect submission vs next-step buttons
        submit_btn = None
        for selector in [
            'button:has-text("Submit application")',
            'button:has-text("Submit")',
            'button:has-text("Apply")',
        ]:
            loc = page.locator(selector)
            if loc.count() > 0 and loc.first.is_visible():
                submit_btn = loc.first
                break

        next_btn = None
        for selector in [
            'button:has-text("Next")',
            'button:has-text("Continue")',
            'button:has-text("Review")',
        ]:
            loc = page.locator(selector)
            if loc.count() > 0 and loc.first.is_visible():
                next_btn = loc.first
                break

        if submit_btn:
            log.info(f"Clicking submit on step {step + 1}")
            human_click(page, submit_btn)
            human_delay(2.0, 4.0)

            # Check for confirmation
            page_text = ""
            try:
                page_text = page.inner_text("body").lower()
            except Exception:
                pass
            success_keywords = ["application submitted", "you've applied", "successfully applied",
                                 "application sent", "we received your application"]
            if any(kw in page_text for kw in success_keywords):
                log.info("Easy Apply — application submitted successfully")
                return {"success": True, "method": "easy_apply", "reason": None, "error": None}

            # Modal may have closed (no confirmation text visible)
            modal = page.locator('.jobs-easy-apply-modal, [data-test-modal]')
            if modal.count() == 0:
                log.info("Easy Apply — modal closed, assuming submitted")
                return {"success": True, "method": "easy_apply", "reason": None, "error": None}

        elif next_btn:
            log.info(f"Easy Apply step {step + 1} — clicking Next/Continue")
            human_click(page, next_btn)
            human_delay(min_delay, max_delay)
        else:
            log.warning(f"Easy Apply step {step + 1} — no Next or Submit button found")
            break

    return {"success": False, "method": "easy_apply", "reason": "modal_stuck",
            "error": "Could not complete Easy Apply modal after 10 steps"}


# ---------------------------------------------------------------------------
# Extract external apply URL from LinkedIn job page
# ---------------------------------------------------------------------------

def _extract_external_apply_url(page: Page) -> Optional[str]:
    """
    On a LinkedIn job page that has no Easy Apply, find the external "Apply" button
    and return the URL it redirects to.  Returns None if not found.
    """
    for selector in [
        'a[data-tracking-control-name*="apply"]',
        'a:has-text("Apply on company website")',
        'a:has-text("Apply")',
        'a[href*="/jobs/apply"]',
        'a[href*="apply"]',
    ]:
        try:
            loc = page.locator(selector).first
            if loc.count() == 0 or not loc.is_visible():
                continue
            href = loc.get_attribute("href") or ""
            if href and "linkedin.com" not in href:
                log.info(f"External apply URL found: {href}")
                return href
        except Exception:
            continue

    # Fallback: click Apply and capture the navigation/popup URL
    for selector in ['a:has-text("Apply")', 'button:has-text("Apply")']:
        try:
            loc = page.locator(selector).first
            if loc.count() == 0 or not loc.is_visible():
                continue
            with page.expect_popup(timeout=5000) as popup_info:
                loc.click()
            popup = popup_info.value
            url = popup.url
            popup.close()
            if url and "linkedin.com" not in url:
                log.info(f"External apply URL via popup: {url}")
                return url
        except Exception:
            continue

    return None


# ---------------------------------------------------------------------------
# External Form (Vision-guided)
# ---------------------------------------------------------------------------

def apply_external_form(
    page: Page,
    apply_url: str,
    resume_path: str,
    profile: dict,
    min_delay: float = 1.5,
    max_delay: float = 4.0,
) -> dict:
    """
    Vision-guided form fill using Claude with a full-page screenshot.
    Returns dict: {"success": bool, "method": "external_form", "error": str|None}
    """
    # ── Step 1: Try ambient agent browser (claude / openclaw CLI) ────────────
    # When running inside Claude Code or openclaw, the agent can browse and fill
    # the form natively using its own tools — no vision API key needed.
    log.info("Attempting agent browser handoff (claude/openclaw CLI)")
    agent_result = call_agent_browser(apply_url, resume_path, profile)
    if agent_result is not None:
        # Agent CLI was available and handled the task (success or failure)
        log.info(f"Agent browser result: {agent_result}")
        return agent_result

    # ── Step 2: Vision-guided Playwright fill ─────────────────────────────────
    # No agent CLI available — fall back to screenshot + vision model
    log.info("No agent CLI available — using vision-guided Playwright fill")
    log.info(f"Navigating to external form: {apply_url}")
    page.goto(apply_url, wait_until="domcontentloaded", timeout=60000)
    human_delay(min_delay, max_delay)

    if _detect_captcha(page):
        return {"success": False, "method": "external_form",
                "error": "CAPTCHA detected — apply manually"}

    if _detect_linkedin_auth_wall(page):
        return {"success": False, "method": "external_form", "reason": "linkedin_auth_required",
                "error": "LinkedIn sign-in wall — browser session not authenticated"}

    # Scroll down to load lazy form elements
    page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
    human_delay(0.5, 1.0)

    # Screenshot → base64
    screenshot_bytes = page.screenshot(full_page=True)
    screenshot_b64 = base64.standard_b64encode(screenshot_bytes).decode()

    profile_json = json.dumps({
        k: v for k, v in profile.items()
        if k in ("full_name", "email", "phone", "linkedin_url", "github_url",
                  "location", "work_authorization", "years_of_experience", "current_title")
    }, indent=2)

    # Vision call — requires Anthropic SDK (image input). If unavailable, do a blind fill.
    actions = None
    prompt = (
        "Here is a screenshot of a job application form. "
        "Based on the applicant profile below, return ONLY a JSON array of actions "
        "to fill this form. No explanation, no markdown fences.\n\n"
        'Each action: {"action": "fill"|"select"|"upload"|"click", '
        '"label": "<field label or button text>", "value": "<value>"}\n\n'
        f"For file upload fields use action=upload and value={resume_path!r}\n"
        "For the submit button use action=click and label=the button text.\n\n"
        f"Applicant profile:\n{profile_json}\n\n"
        "Return only the JSON array."
    )
    try:
        raw = call_claude(
            prompt,
            system="You are helping fill out a job application form.",
            image_b64=screenshot_b64,
        )
        actions = json.loads(strip_json_fences(raw))
        log.info(f"Claude returned {len(actions)} form actions")
    except Exception as exc:
        log.warning(
            f"Vision-based form analysis unavailable ({exc}). "
            "Falling back to blind field fill (no API key / vision not supported by CLI). "
            "Set ANTHROPIC_API_KEY in .env to enable vision-guided form filling."
        )
        # Blind fill: fill common fields by label, upload resume, click submit
        _fill_form_from_profile(page, profile)
        try:
            file_input = page.locator('input[type="file"]').first
            file_input.set_input_files(resume_path)
            human_delay(1.0, 2.0)
        except Exception:
            pass
        for submit_label in ["Submit", "Apply Now", "Apply", "Submit Application"]:
            btn = page.locator(f'button:has-text("{submit_label}"), input[value="{submit_label}"]')
            if btn.count() > 0 and btn.first.is_visible():
                human_click(page, btn.first)
                human_delay(2.0, 3.0)
                break
        human_delay(2.0, 4.0)
        page_text = ""
        try:
            page_text = page.inner_text("body").lower()
        except Exception:
            pass
        success_keywords = ["application submitted", "thank you for applying", "successfully applied",
                            "application received", "we'll be in touch"]
        success = any(kw in page_text for kw in success_keywords)
        if not success:
            return {"success": False, "method": "external_form",
                    "error": "Vision unavailable — blind fill attempted. Add ANTHROPIC_API_KEY to .env for full support. Mark as manual_required."}
        return {"success": True, "method": "external_form_blind", "error": None}

    if actions is None:
        return {"success": False, "method": "external_form",
                "error": "No form actions returned"}

    log.info(f"Claude returned {len(actions)} form actions")

    for action in actions:
        act  = action.get("action", "")
        label = action.get("label", "")
        value = action.get("value", "")
        try:
            if act == "fill":
                loc = find_by_label(page, label)
                if loc:
                    loc.fill(str(value))
                    human_delay(0.3, 0.8)
            elif act == "select":
                loc = find_by_label(page, label)
                if loc:
                    loc.select_option(label=str(value))
                    human_delay(0.3, 0.8)
            elif act == "upload":
                loc = find_by_label(page, label) or page.locator('input[type="file"]').first
                if loc:
                    loc.set_input_files(value)
                    human_delay(1.0, 2.0)
            elif act == "click":
                btn = page.locator(f'button:has-text("{label}"), input[value="{label}"]')
                if btn.count() > 0:
                    human_click(page, btn.first)
                    human_delay(1.5, 3.0)
        except Exception as exc:
            log.warning(f"Action failed [{act} '{label}']: {exc}")

    human_delay(2.0, 4.0)

    # Check for confirmation
    if _detect_captcha(page):
        return {"success": False, "method": "external_form",
                "error": "CAPTCHA appeared after submit — apply manually"}

    page_text = ""
    try:
        page_text = page.inner_text("body").lower()
    except Exception:
        pass

    success_keywords = [
        "application submitted", "thank you for applying", "successfully applied",
        "application received", "we'll be in touch", "application complete",
    ]
    success = any(kw in page_text for kw in success_keywords)
    return {
        "success": success,
        "method": "external_form",
        "error": None if success else "No confirmation text detected after submit",
    }


# ---------------------------------------------------------------------------
# LinkedIn session management
# ---------------------------------------------------------------------------

def _save_linkedin_cookies(context: BrowserContext) -> None:
    """Persist browser cookies to disk for reuse across runs."""
    cookies = context.cookies()
    LINKEDIN_SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    LINKEDIN_SESSION_FILE.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
    log.info(f"LinkedIn session saved ({len(cookies)} cookies → {LINKEDIN_SESSION_FILE.name})")


def _load_linkedin_cookies(context: BrowserContext, page: Page) -> bool:
    """
    Load saved cookies into the browser context and verify the session is still valid.
    Returns True if logged in, False if cookies are missing or expired.
    """
    if not LINKEDIN_SESSION_FILE.exists():
        log.info("No saved LinkedIn session found")
        return False

    try:
        cookies = json.loads(LINKEDIN_SESSION_FILE.read_text(encoding="utf-8"))
        context.add_cookies(cookies)
        log.info(f"Loaded {len(cookies)} LinkedIn cookies — verifying session")

        page.goto("https://www.linkedin.com/feed", wait_until="domcontentloaded", timeout=30000)
        human_delay(2.0, 3.0)

        # Logged-in indicator: global nav bar is present
        if page.locator(".global-nav__me, [data-test-global-nav-me]").count() > 0:
            log.info("LinkedIn session is valid — skipping login")
            return True

        # Feed URL without redirect means we're in — check URL as backup
        if "/feed" in page.url and "login" not in page.url:
            log.info("LinkedIn session valid (feed URL confirmed)")
            return True

        log.info("LinkedIn session expired — will re-login")
        LINKEDIN_SESSION_FILE.unlink(missing_ok=True)
        return False

    except Exception as exc:
        log.warning(f"Cookie load failed: {exc} — will re-login")
        LINKEDIN_SESSION_FILE.unlink(missing_ok=True)
        return False


def login_to_linkedin(
    page: Page,
    context: BrowserContext,
    email: str,
    password: str,
) -> dict:
    """
    Log in to LinkedIn with email + password and save cookies on success.
    Returns {"success": True} or {"success": False, "reason": str, "error": str}.
    """
    log.info("Logging in to LinkedIn")
    page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=30000)
    human_delay(1.5, 2.5)

    try:
        page.fill("#username", email)
        human_delay(0.4, 0.8)
        page.fill("#password", password)
        human_delay(0.4, 0.8)

        # Click sign-in button (try multiple selectors)
        for selector in [
            '[data-litms-control-urn="login-submit"]',
            'button[type="submit"]',
            'button:has-text("Sign in")',
        ]:
            btn = page.locator(selector)
            if btn.count() > 0:
                human_click(page, btn.first)
                break

        human_delay(4.0, 6.0)
        current_url = page.url

        # 2FA / verification checkpoint
        if "checkpoint" in current_url or "challenge" in current_url:
            log.warning("LinkedIn 2FA/checkpoint triggered")
            return {
                "success": False,
                "reason": "2fa_required",
                "error": "LinkedIn requires 2FA — disable it on your account or apply manually",
            }

        # Wrong credentials
        error_el = page.locator('.alert--error, #error-for-password, .form__label--error')
        if error_el.count() > 0:
            msg = error_el.first.inner_text().strip()
            return {
                "success": False,
                "reason": "wrong_credentials",
                "error": f"LinkedIn login failed: {msg}. Check LINKEDIN_EMAIL/PASSWORD in .env",
            }

        # Success — feed or home page
        if "feed" in current_url or "mynetwork" in current_url or page.locator(".global-nav__me").count() > 0:
            _save_linkedin_cookies(context)
            log.info("LinkedIn login successful")
            return {"success": True}

        # Unknown state — assume logged in (some accounts land on different pages)
        log.info(f"LinkedIn login — unknown redirect to {current_url}, assuming success")
        _save_linkedin_cookies(context)
        return {"success": True}

    except Exception as exc:
        return {"success": False, "reason": "login_error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_application(
    job: dict,
    resume_path: str,
    profile: dict,
    headless: bool = True,
    slow_mo: int = 50,
    min_delay: float = 1.5,
    max_delay: float = 4.0,
) -> dict:
    """
    Launch browser, determine apply strategy (Easy Apply vs external),
    attempt application. Returns result dict with success/method/error.
    """
    apply_url = job.get("apply_url") or ""

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            slow_mo=slow_mo,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = browser.new_context(
            user_agent=STEALTH_USER_AGENT,
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        page = context.new_page()
        page.add_init_script(STEALTH_SCRIPT)

        try:
            if "linkedin.com" in apply_url:
                # ── LinkedIn session management ──────────────────────────────
                li_email    = os.environ.get("LINKEDIN_EMAIL", "").strip()
                li_password = os.environ.get("LINKEDIN_PASSWORD", "").strip()

                if li_email and li_password:
                    # Try saved cookies first; re-login only if expired/missing
                    if not _load_linkedin_cookies(context, page):
                        login_result = login_to_linkedin(page, context, li_email, li_password)
                        if not login_result["success"]:
                            reason = login_result.get("reason", "")
                            if reason == "2fa_required":
                                return {
                                    "success": False,
                                    "method": "linkedin",
                                    "reason": "2fa_required",
                                    "error": login_result["error"],
                                }
                            # Wrong credentials or unknown — log and continue
                            # (apply attempt will hit auth wall and be caught below)
                            log.warning(f"LinkedIn login failed: {login_result.get('error')}")
                else:
                    log.info(
                        "LINKEDIN_EMAIL/PASSWORD not set in .env — "
                        "proceeding without login (Easy Apply jobs may hit auth wall)"
                    )
                # ── Apply ────────────────────────────────────────────────────
                result = apply_linkedin_easy_apply(
                    page, apply_url, resume_path, profile, min_delay, max_delay
                )
                # If no Easy Apply button, extract the real external URL then apply
                if result.get("reason") == "no_easy_apply":
                    log.info("No Easy Apply — extracting external apply URL")
                    external_url = _extract_external_apply_url(page) or apply_url
                    log.info(f"External apply URL: {external_url}")
                    result = apply_external_form(
                        page, external_url, resume_path, profile, min_delay, max_delay
                    )
            else:
                result = apply_external_form(
                    page, apply_url, resume_path, profile, min_delay, max_delay
                )
        except Exception as exc:
            log.error(f"run_application unhandled exception: {exc}")
            result = {"success": False, "method": "unknown", "error": str(exc)}
        finally:
            try:
                context.close()
                browser.close()
            except Exception:
                pass

    return result
