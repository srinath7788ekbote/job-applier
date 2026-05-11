"""
apply_jobs.py
Browser automation for job applications using Playwright (sync API).

Supports:
  - LinkedIn Easy Apply (multi-step modal)
  - External application forms (DOM agent-guided)

CAPTCHA detection: if detected in headless mode, marks job as manual_required
so the user can apply manually later.
"""

import json
import logging
import os
import random
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from claude_client import call_llm, strip_json_fences
from dom_agent import dom_agent_fill, extract_dom_state, extract_validation_errors
from playwright.sync_api import sync_playwright, Page, BrowserContext, Locator

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
CHROME_PROFILE_DIR = BASE_DIR / "data" / "chrome_profile"

STEALTH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
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
    '.authwall-join-form',                 # full-page authwall form
    'form.login__form',                    # login form on /login page
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
    # Escape quotes to prevent XPath/CSS injection (e.g. names like O'Brien)
    xpath_safe = text_lower.replace("'", "\\'")
    css_safe = label_text.replace('"', '\\"')
    css_safe_lower = text_lower.replace('"', '\\"')
    strategies = [
        # By associated label element text
        f'//label[contains(translate(text(),"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"),"{xpath_safe}")]',
        # By placeholder
        f'input[placeholder*="{css_safe}" i], textarea[placeholder*="{css_safe}" i]',
        # By aria-label
        f'input[aria-label*="{css_safe}" i], textarea[aria-label*="{css_safe}" i], select[aria-label*="{css_safe}" i]',
        # By name attribute
        f'input[name*="{css_safe_lower}"], select[name*="{css_safe_lower}"], textarea[name*="{css_safe_lower}"]',
        # By id
        f'input[id*="{css_safe_lower}"], select[id*="{css_safe_lower}"], textarea[id*="{css_safe_lower}"]',
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
    """Return True if the page contains CAPTCHA indicators (text or iframes)."""
    try:
        text = page.inner_text("body").lower()
        if any(pattern in text for pattern in CAPTCHA_PATTERNS):
            return True
    except Exception:
        pass
    # Check for reCAPTCHA / hCaptcha iframes
    try:
        captcha_iframes = page.locator(
            'iframe[src*="recaptcha"], iframe[src*="hcaptcha"], '
            'iframe[title*="reCAPTCHA"], iframe[title*="hCaptcha"], '
            'iframe[src*="captcha"], iframe[src*="challenge"]'
        )
        if captcha_iframes.count() > 0:
            return True
    except Exception:
        pass
    return False


def _detect_linkedin_auth_wall(page: Page) -> bool:
    """Return True if LinkedIn is showing a sign-in wall blocking the apply flow."""
    # If user avatar is visible in the nav, we are logged in — not an auth wall
    try:
        if page.locator(".global-nav__me, [data-test-global-nav-me]").count() > 0:
            return False
    except Exception:
        pass

    # Check URL — login/authwall pages are definitive
    current_url = page.url.lower()
    if any(kw in current_url for kw in ["/login", "/authwall", "/uas/login"]):
        return True

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
    """Fill all visible form fields using profile data.

    Uses the email from the resume profile (not the LinkedIn login email).
    LinkedIn credentials are only for authentication, never for applications.
    """
    # Use resume email for applications, never the LinkedIn login email
    application_email = profile.get("email", "")
    li_email = os.environ.get("LINKEDIN_EMAIL", "").strip()
    if application_email and application_email.lower() == li_email.lower():
        log.warning(
            "Profile email matches LINKEDIN_EMAIL — these should be different. "
            "Job applications should use the email from your resume, not LinkedIn login."
        )

    field_map = {
        "first name":       profile.get("full_name", "").split()[0] if profile.get("full_name") else "",
        "last name":        profile.get("full_name", "").split()[-1] if profile.get("full_name") else "",
        "full name":        profile.get("full_name", ""),
        "name":             profile.get("full_name", ""),
        "email":            application_email,
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
    resume_text: str = "",
    applicant_qa: dict | None = None,
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
        # Auth wall hit — but the job may still have an external apply URL embedded in the page.
        # Try to extract it and hand off to the claude CLI agent rather than giving up.
        log.info("LinkedIn auth wall detected — attempting to extract external apply URL for agent handoff")
        external_url = _extract_external_apply_url(page)
        if external_url:
            log.info(f"External URL found despite auth wall: {external_url} — handing off to agent")
            return apply_external_form(page, external_url, resume_path, profile, min_delay, max_delay, resume_text, applicant_qa)
        # No external URL extractable — fall back to the job URL itself for agent handoff
        log.info("No external URL found — handing off job URL directly to agent")
        return apply_external_form(page, job_url, resume_path, profile, min_delay, max_delay, resume_text, applicant_qa)

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

        # Fill visible form fields (static profile mapping first)
        _fill_form_from_profile(page, profile)

        # DOM agent fallback: handle unfilled required fields that profile mapping missed
        errors = extract_validation_errors(page)
        modal_elements = extract_dom_state(page)
        unfilled_required = [
            el for el in modal_elements
            if el.get("required") and not el.get("value")
            and el.get("tag") in ("input", "select", "textarea")
            and el.get("type") != "file"
        ]
        if unfilled_required or errors:
            log.info(f"Easy Apply step {step + 1}: {len(unfilled_required)} unfilled required fields, {len(errors)} errors — asking Claude")
            try:
                from dom_agent import build_form_prompt, execute_action
                prompt = build_form_prompt(
                    modal_elements, profile, resume_path,
                    page.url, f"Easy Apply Step {step + 1}", errors, step, 10,
                    resume_text=resume_text, applicant_qa=applicant_qa,
                )
                raw = call_llm(prompt, system="You are filling a job application form on behalf of the applicant.")
                actions = json.loads(strip_json_fences(raw))
                for action in actions:
                    if isinstance(action, dict) and action.get("action") not in ("done", "click"):
                        execute_action(page, modal_elements, action, resume_path)
            except Exception as exc:
                log.warning(f"DOM agent fallback in Easy Apply failed: {exc}")

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
            # Check for validation errors before clicking Next
            pre_click_errors = extract_validation_errors(page)
            log.info(f"Easy Apply step {step + 1} — clicking Next/Continue")
            human_click(page, next_btn)
            human_delay(min_delay, max_delay)

            # After clicking Next, check if we're stuck on same page due to errors
            post_click_errors = extract_validation_errors(page)
            if post_click_errors and post_click_errors == pre_click_errors:
                log.warning(f"Easy Apply step {step + 1}: stuck on validation errors: {post_click_errors}")
                # Page didn't advance — errors still there, will be handled next loop iteration
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
    resume_text: str = "",
    applicant_qa: dict | None = None,
) -> dict:
    """
    Fill an external job application form using DOM-aware agent.

    Strategy:
      1. Navigate to the form URL
      2. Use DOM agent (extract elements + screenshot → Claude CLI → execute)
      3. Signal agent_handoff_required only for CAPTCHA / auth walls

    Returns dict: {"success": bool, "method": str, "error": str|None}
    """
    log.info(f"Navigating to external form: {apply_url}")
    page.goto(apply_url, wait_until="domcontentloaded", timeout=60000)
    human_delay(min_delay, max_delay)

    if _detect_captcha(page):
        return _signal_handoff(apply_url, resume_path, "CAPTCHA detected on external form")

    if _detect_linkedin_auth_wall(page):
        return _signal_handoff(apply_url, resume_path, "LinkedIn sign-in wall on external form")

    # If the URL is still a LinkedIn page (no real external form), skip direct fill
    if "linkedin.com" in page.url:
        log.info("External URL resolved to LinkedIn — handing off to CLI agent")
        return _signal_handoff(apply_url, resume_path, "No external form found — LinkedIn page")

    # Scroll to load lazy form elements
    page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
    human_delay(0.5, 1.0)

    # ── DOM Agent fill (DOM state + screenshot → Claude CLI → execute actions)
    return dom_agent_fill(page, apply_url, resume_path, profile,
                          resume_text=resume_text, applicant_qa=applicant_qa)


def _signal_handoff(apply_url: str, resume_path: str, reason: str) -> dict:
    """Signal that an agent should handle this form (CAPTCHA / auth wall)."""
    log.info(f"Agent handoff required: {reason}")
    return {
        "success": False,
        "method": "agent_handoff_required",
        "url": apply_url,
        "resume_path": resume_path,
        "error": reason,
    }


# ---------------------------------------------------------------------------
# Persistent browser session
# ---------------------------------------------------------------------------

@contextmanager
def open_persistent_browser(
    headless: bool = True,
    slow_mo: int = 50,
):
    """Open a persistent Chromium profile that remembers sessions across runs.

    Uses a user-data-dir (like EasyApplyBot's chrome_bot/) so cookies,
    localStorage, and sessions survive across pipeline runs.  After the
    first manual login the browser is already authenticated.

    Yields (context, page).
    """
    CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(CHROME_PROFILE_DIR),
            headless=headless,
            slow_mo=slow_mo,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
            user_agent=STEALTH_USER_AGENT,
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        context.add_init_script(STEALTH_SCRIPT)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            yield context, page
        finally:
            context.close()


def ensure_linkedin_login(page: Page, context: BrowserContext) -> bool:
    """Check persistent profile for a valid LinkedIn session; login if needed.

    Returns True if logged in, False otherwise.
    """
    li_email = os.environ.get("LINKEDIN_EMAIL", "").strip()
    li_password = os.environ.get("LINKEDIN_PASSWORD", "").strip()

    if not li_email or not li_password:
        log.info(
            "LINKEDIN_EMAIL/PASSWORD not set in .env — "
            "proceeding without login (auth wall will trigger agent handoff)"
        )
        return False

    # Persistent profile may already have a valid session
    try:
        page.goto(
            "https://www.linkedin.com/feed",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        human_delay(2.0, 3.0)

        if page.locator(".global-nav__me, [data-test-global-nav-me]").count() > 0:
            log.info("Already logged in via persistent browser profile")
            return True

        if "/feed" in page.url and "login" not in page.url:
            log.info("Already logged in (feed URL confirmed)")
            return True
    except Exception as exc:
        log.warning(f"Feed check failed: {exc}")

    # No valid session — login with credentials
    log.info("No valid session in browser profile — logging in")
    result = login_to_linkedin(page, context, li_email, li_password)
    return result.get("success", False)


def login_to_linkedin(
    page: Page,
    context: BrowserContext,
    email: str,
    password: str,
) -> dict:
    """
    Log in to LinkedIn with email + password.
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
            log.info("LinkedIn login successful")
            return {"success": True}

        # Unknown state — assume logged in (some accounts land on different pages)
        log.info(f"LinkedIn login — unknown redirect to {current_url}, assuming success")
        return {"success": True}

    except Exception as exc:
        return {"success": False, "reason": "login_error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Apply to a single job (reuses an existing browser session)
# ---------------------------------------------------------------------------

def apply_single_job(
    page: Page,
    context: BrowserContext,
    job: dict,
    resume_path: str,
    profile: dict,
    min_delay: float = 1.5,
    max_delay: float = 4.0,
    resume_text: str = "",
    applicant_qa: dict | None = None,
) -> dict:
    """Apply to one job using an already-open browser session."""
    apply_url = job.get("apply_url") or ""

    try:
        if "linkedin.com" in apply_url:
            result = apply_linkedin_easy_apply(
                page, apply_url, resume_path, profile, min_delay, max_delay,
                resume_text=resume_text, applicant_qa=applicant_qa,
            )
            if result.get("reason") == "no_easy_apply":
                log.info("No Easy Apply — extracting external apply URL")
                external_url = _extract_external_apply_url(page) or apply_url
                log.info(f"External apply URL: {external_url}")
                result = apply_external_form(
                    page, external_url, resume_path, profile, min_delay, max_delay,
                    resume_text=resume_text, applicant_qa=applicant_qa,
                )
        else:
            result = apply_external_form(
                page, apply_url, resume_path, profile, min_delay, max_delay,
                resume_text=resume_text, applicant_qa=applicant_qa,
            )
    except Exception as exc:
        log.error(f"apply_single_job unhandled exception: {exc}")
        result = {"success": False, "method": "unknown", "error": str(exc)}

    return result


# ---------------------------------------------------------------------------
# Standalone entry point (opens its own browser, applies to one job)
# ---------------------------------------------------------------------------

def run_application(
    job: dict,
    resume_path: str,
    profile: dict,
    headless: bool = True,
    slow_mo: int = 50,
    min_delay: float = 1.5,
    max_delay: float = 4.0,
    resume_text: str = "",
    applicant_qa: dict | None = None,
) -> dict:
    """
    Open a persistent browser, log in if needed, apply to one job, close.
    For batch use, prefer open_persistent_browser() + apply_single_job().
    """
    with open_persistent_browser(headless=headless, slow_mo=slow_mo) as (context, page):
        apply_url = job.get("apply_url") or ""
        if "linkedin.com" in apply_url:
            ensure_linkedin_login(page, context)
        return apply_single_job(
            page, context, job, resume_path, profile, min_delay, max_delay,
            resume_text=resume_text, applicant_qa=applicant_qa,
        )
