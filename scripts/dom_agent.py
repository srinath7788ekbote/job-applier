"""
dom_agent.py
DOM-aware form filling agent using Playwright + Claude CLI.

Extracts interactive elements from the page as a structured list,
sends both DOM state and a screenshot to Claude CLI for decision-making,
then executes the returned actions via Playwright element handles.
"""

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page, ElementHandle

from claude_client import call_llm, strip_json_fences

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Component 1: DOM State Extraction
# ---------------------------------------------------------------------------

# Selectors for interactive elements
INTERACTIVE_SELECTORS = (
    "input, select, textarea, button, "
    "[role='button'], [role='checkbox'], [role='radio'], "
    "[role='combobox'], [role='listbox'], [role='option'], "
    "[contenteditable='true'], a[href]"
)

# Elements to skip (nav links, social, etc.)
SKIP_LINK_PATTERNS = [
    "linkedin.com", "twitter.com", "facebook.com", "instagram.com",
    "privacy", "terms", "cookie", "sitemap", "careers-home",
]


def _get_element_label(page: Page, handle: ElementHandle) -> str:
    """Discover the label for an element using multiple strategies."""
    try:
        label = handle.evaluate("""el => {
            // Strategy 1: aria-label
            if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
            
            // Strategy 2: associated label via id
            if (el.id) {
                const label = document.querySelector('label[for="' + el.id + '"]');
                if (label) return label.textContent.trim();
            }
            
            // Strategy 3: wrapping label
            const parentLabel = el.closest('label');
            if (parentLabel) {
                // Get label text excluding the input's own text
                const clone = parentLabel.cloneNode(true);
                const inputs = clone.querySelectorAll('input, select, textarea');
                inputs.forEach(i => i.remove());
                const text = clone.textContent.trim();
                if (text) return text;
            }
            
            // Strategy 4: preceding label sibling
            const prev = el.previousElementSibling;
            if (prev && prev.tagName === 'LABEL') return prev.textContent.trim();
            
            // Strategy 5: parent .form-group / .field label
            const group = el.closest('.form-group, .form-field, .field, fieldset, [class*="field"]');
            if (group) {
                const lbl = group.querySelector('label, legend, .label, [class*="label"]');
                if (lbl) return lbl.textContent.trim();
            }
            
            // Strategy 6: aria-labelledby
            const labelledBy = el.getAttribute('aria-labelledby');
            if (labelledBy) {
                const referenced = document.getElementById(labelledBy);
                if (referenced) return referenced.textContent.trim();
            }
            
            // Strategy 7: title attribute
            if (el.title) return el.title;
            
            return '';
        }""")
        return (label or "").strip()[:100]
    except Exception:
        return ""


def _get_element_info(page: Page, handle: ElementHandle) -> Optional[dict]:
    """Extract structured info from a single element handle."""
    try:
        info = handle.evaluate("""el => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            
            // Skip invisible elements
            if (rect.width === 0 && rect.height === 0) return null;
            if (style.display === 'none' || style.visibility === 'hidden') return null;
            if (parseFloat(style.opacity) === 0) return null;
            
            // Skip disabled elements
            if (el.disabled) return null;
            
            const tag = el.tagName.toLowerCase();
            const type = (el.type || '').toLowerCase();
            
            // Get options for select elements
            let options = null;
            if (tag === 'select') {
                options = Array.from(el.options).map(o => ({
                    value: o.value,
                    text: o.text.trim(),
                    selected: o.selected
                }));
            }
            
            // Get text content for buttons/links
            let text = '';
            if (tag === 'button' || el.getAttribute('role') === 'button' || tag === 'a') {
                text = el.textContent.trim().substring(0, 80);
            }
            
            return {
                tag: tag,
                type: type,
                name: el.name || '',
                id: el.id || '',
                placeholder: el.placeholder || '',
                value: (tag === 'select') ? 
                    (el.options[el.selectedIndex]?.text || '') :
                    (el.value || ''),
                required: el.required || el.getAttribute('aria-required') === 'true',
                options: options,
                text: text,
                checked: el.checked || false,
                href: (tag === 'a') ? el.href : '',
                readonly: el.readOnly || false,
                autocomplete: el.getAttribute('autocomplete') || '',
            };
        }""")
        
        if info is None:
            return None
        
        # Skip navigation links that aren't form-related
        if info["tag"] == "a":
            href = info.get("href", "").lower()
            text = info.get("text", "").lower()
            if any(p in href for p in SKIP_LINK_PATTERNS):
                return None
            # Only keep links that look like buttons (Apply, Submit, etc.)
            if not any(kw in text for kw in ["apply", "submit", "next", "continue", "upload", "browse"]):
                return None
        
        # Skip hidden inputs
        if info["tag"] == "input" and info["type"] == "hidden":
            return None
        
        # Get label via the multi-strategy function
        info["label"] = _get_element_label(page, handle)
        
        # Fall back to placeholder or name if no label
        if not info["label"]:
            info["label"] = info["placeholder"] or info["name"] or info["id"]
        
        # Store the handle for later interaction
        info["_handle"] = handle
        
        return info
        
    except Exception as exc:
        log.debug(f"Element info extraction failed: {exc}")
        return None


def extract_dom_state(page: Page) -> list[dict]:
    """
    Extract all interactive elements from the page as a structured list.
    Each element has metadata + a Playwright ElementHandle for interaction.
    """
    elements = []
    try:
        handles = page.query_selector_all(INTERACTIVE_SELECTORS)
    except Exception as exc:
        log.warning(f"DOM query failed: {exc}")
        return elements
    
    for handle in handles:
        info = _get_element_info(page, handle)
        if info is not None:
            elements.append(info)
    
    log.info(f"Extracted {len(elements)} interactive elements from page")
    return elements


# ---------------------------------------------------------------------------
# Component 2: Validation Error Extraction
# ---------------------------------------------------------------------------

ERROR_SELECTORS = [
    ".error", ".field-error", ".form-error", ".validation-error",
    ".invalid-feedback", ".error-message", ".form__label--error",
    "[role='alert']", ".alert-danger", ".alert--error",
    ".artdeco-inline-feedback--error",
    ".error-text", ".has-error .help-block",
    "[class*='error-msg']", "[class*='err-msg']",
    "[aria-invalid='true'] ~ .error",
]


def extract_validation_errors(page: Page) -> list[str]:
    """Extract visible validation error messages from the page."""
    errors = []
    selector = ", ".join(ERROR_SELECTORS)
    try:
        error_elements = page.query_selector_all(selector)
        for el in error_elements:
            try:
                if not el.is_visible():
                    continue
                text = el.text_content()
                if text and text.strip():
                    msg = text.strip()[:200]
                    if msg not in errors:
                        errors.append(msg)
            except Exception:
                continue
    except Exception as exc:
        log.debug(f"Error extraction failed: {exc}")
    return errors


# ---------------------------------------------------------------------------
# Component 3: LLM Prompt Formatter
# ---------------------------------------------------------------------------

def _format_element_for_prompt(index: int, el: dict) -> str:
    """Format a single element as a human-readable line for the LLM prompt."""
    tag = el["tag"]
    type_str = f' type="{el["type"]}"' if el.get("type") else ""
    name_str = f' name="{el["name"]}"' if el.get("name") else ""
    label_str = f' label="{el["label"]}"' if el.get("label") else ""
    placeholder_str = f' placeholder="{el["placeholder"]}"' if el.get("placeholder") else ""
    required_str = " required" if el.get("required") else ""
    readonly_str = " readonly" if el.get("readonly") else ""
    
    line = f"[{index}] <{tag}{type_str}{name_str}{label_str}{placeholder_str}{required_str}{readonly_str}>"
    
    # Add current value
    if el.get("value"):
        line += f' value="{el["value"]}"'
    else:
        line += ' value=""'
    
    # Add options for select elements
    if el.get("options"):
        opt_texts = [o["text"] for o in el["options"] if o["text"]][:15]
        selected = next((o["text"] for o in el["options"] if o["selected"]), "")
        line += f' options={json.dumps(opt_texts)} selected="{selected}"'
    
    # Add text for buttons
    if el.get("text"):
        line += f' "{el["text"]}"'
    
    # Add checked state for checkboxes/radios
    if el["tag"] == "input" and el.get("type") in ("checkbox", "radio"):
        line += f' checked={el["checked"]}'
    
    return line


def build_form_prompt(
    elements: list[dict],
    profile: dict,
    resume_path: str,
    page_url: str,
    page_title: str,
    errors: list[str],
    step: int,
    max_steps: int = 8,
    resume_text: str = "",
    applicant_qa: dict | None = None,
) -> str:
    """Build the full prompt for Claude CLI to decide form-filling actions."""
    
    # Profile block
    profile_lines = []
    field_labels = [
        ("full_name", "Full Name"),
        ("email", "Email"),
        ("phone", "Phone"),
        ("linkedin_url", "LinkedIn URL"),
        ("github_url", "GitHub URL"),
        ("portfolio_url", "Portfolio URL"),
        ("current_title", "Current Title"),
        ("location", "Location"),
        ("work_authorization", "Work Authorization"),
        ("years_of_experience", "Years of Experience"),
    ]
    for key, label in field_labels:
        val = profile.get(key)
        if val:
            profile_lines.append(f"  {label}: {val}")
    profile_block = "\n".join(profile_lines)
    
    # Applicant Q&A block (pre-filled answers for common questions)
    qa_block = ""
    if applicant_qa:
        qa_lines = []
        for key, val in applicant_qa.items():
            if isinstance(val, list):
                # e.g. languages list
                for item in val:
                    if isinstance(item, dict):
                        qa_lines.append(f"  {key}: {', '.join(f'{k}={v}' for k,v in item.items())}")
                    else:
                        qa_lines.append(f"  {key}: {item}")
            elif isinstance(val, bool):
                qa_lines.append(f"  {key.replace('_', ' ').title()}: {'Yes' if val else 'No'}")
            else:
                qa_lines.append(f"  {key.replace('_', ' ').title()}: {val}")
        qa_block = "\nPRE-CONFIGURED ANSWERS (use these for common questions):\n" + "\n".join(qa_lines)
    
    # Resume text block (truncated to avoid token explosion)
    resume_block = ""
    if resume_text:
        truncated = resume_text[:4000]
        resume_block = f"\nRESUME CONTENT (use this to answer experience/skill questions):\n{truncated}"
    
    # Elements block
    elements_block = "\n".join(
        _format_element_for_prompt(i, el) for i, el in enumerate(elements)
    )
    
    # Errors block
    errors_block = ""
    if errors:
        errors_block = "\nVALIDATION ERRORS:\n" + "\n".join(f"  - {e}" for e in errors)
    
    prompt = f"""You are filling a job application form on behalf of a real person. The applicant is a human — you are just helping them save time. Fill the form exactly as they would.

PAGE URL: {page_url}
PAGE TITLE: {page_title}

APPLICANT PROFILE:
{profile_block}
{qa_block}
{resume_block}

INTERACTIVE ELEMENTS:
{elements_block}
{errors_block}

STEP: {step + 1} of {max_steps}

Return ONLY a JSON array of actions. Each action is one of:
- {{"action": "fill", "index": N, "value": "text to type"}}
- {{"action": "select", "index": N, "value": "option text to select"}}
- {{"action": "upload", "index": N}}
- {{"action": "click", "index": N}}
- {{"action": "check", "index": N}}
- {{"action": "done", "success": true/false, "reason": "why"}}

ANSWERING QUESTIONS:
- For profile fields (name, email, phone, location): use the APPLICANT PROFILE values exactly.
- For common questions (notice period, visa, salary, relocation): use PRE-CONFIGURED ANSWERS.
- For experience/skill questions ("how many years of X", "describe your experience with Y"):
  look at the RESUME CONTENT and answer based on what is actually listed there.
- For free-text answers (cover letter, "why this role", motivation): write a SHORT,
  natural, first-person answer based on the resume. Keep it under 3 sentences.
  Sound like a real person — casual-professional tone. No buzzwords, no corporate-speak.
- If a question asks something not in the profile, pre-configured answers, or resume,
  pick the most reasonable/common answer from the available options.
- NEVER mention AI, automation, or that this form is being filled programmatically.

FORM RULES:
- Fill ALL empty required fields. Match fields by label/name/placeholder.
- For file upload fields (type="file"), use action "upload" (the resume path is known).
- After filling all visible fields, include a "click" on the submit/next button.
- If this is a multi-step form, click Next/Continue (not Submit).
- If you see a success/confirmation message, return done with success=true.
- If the form requires CAPTCHA or login, return done with success=false and reason.
- Do NOT fill fields that already have correct values.
- Do NOT click navigation links — only form submit/next buttons.
- If there are validation errors, fix the offending fields first.
- For location fields: city is "{profile.get("location", "").split(",")[0].strip() if profile.get("location") else ""}", country is "{profile.get("location", "").split(",")[-1].strip() if profile.get("location") else ""}".
- For name fields: first name is "{profile.get("full_name", "").split()[0] if profile.get("full_name") else ""}", last name is "{" ".join(profile.get("full_name", "").split()[1:]) if profile.get("full_name") else ""}".

Return ONLY the JSON array, no explanation."""
    
    return prompt


# ---------------------------------------------------------------------------
# Component 4: Action Executor
# ---------------------------------------------------------------------------

def _human_delay(min_s: float = 0.3, max_s: float = 0.8) -> None:
    """Small random delay to appear human."""
    import random
    time.sleep(random.uniform(min_s, max_s))


def execute_action(page: Page, elements: list[dict], action: dict, resume_path: str) -> bool:
    """
    Execute a single action on the page.
    Returns True on success, False on failure.
    """
    act = action.get("action", "")
    index = action.get("index")
    value = action.get("value", "")
    
    if act == "done":
        return True  # Handled by caller
    
    if index is None or index < 0 or index >= len(elements):
        log.warning(f"Invalid element index {index} (have {len(elements)} elements)")
        return False
    
    el = elements[index]
    handle = el.get("_handle")
    if handle is None:
        log.warning(f"No handle for element [{index}]")
        return False
    
    try:
        if act == "fill":
            # Clear and type
            handle.click()
            _human_delay(0.1, 0.3)
            handle.evaluate("el => { el.value = ''; el.dispatchEvent(new Event('input', {bubbles: true})); }")
            handle.type(str(value), delay=30)
            _human_delay(0.2, 0.5)
            return True
            
        elif act == "select":
            tag = el.get("tag", "")
            if tag == "select":
                # Try exact match first
                options = el.get("options", [])
                exact = next((o["value"] for o in options if o["text"].lower() == value.lower()), None)
                if exact is not None:
                    handle.select_option(value=exact)
                else:
                    # Fuzzy: contains match
                    fuzzy = next((o["value"] for o in options if value.lower() in o["text"].lower()), None)
                    if fuzzy is not None:
                        handle.select_option(value=fuzzy)
                    else:
                        # Try by label text
                        handle.select_option(label=value)
                _human_delay(0.2, 0.5)
                return True
            else:
                # Combobox or custom dropdown — type the value
                handle.click()
                _human_delay(0.2, 0.4)
                handle.type(str(value), delay=30)
                _human_delay(0.3, 0.6)
                return True
            
        elif act == "upload":
            handle.set_input_files(resume_path)
            _human_delay(0.5, 1.0)
            return True
            
        elif act == "click":
            handle.scroll_into_view_if_needed()
            _human_delay(0.1, 0.3)
            handle.click()
            _human_delay(0.5, 1.5)
            return True
            
        elif act == "check":
            handle.check()
            _human_delay(0.2, 0.4)
            return True
            
        else:
            log.warning(f"Unknown action: {act}")
            return False
            
    except Exception as exc:
        log.warning(f"Action [{act}] on element [{index}] failed: {exc}")
        return False


# ---------------------------------------------------------------------------
# Component 5: Loop Detection
# ---------------------------------------------------------------------------

def compute_state_signature(url: str, elements: list[dict]) -> str:
    """Hash of page URL + element metadata to detect stuck loops."""
    sig_parts = [url, str(len(elements))]
    for el in elements:
        sig_parts.append(
            f"{el.get('tag','')}:{el.get('name','')}:{el.get('label','')}:{el.get('value','')}"
        )
    return hashlib.md5("|".join(sig_parts).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Component 6: Multi-Step Agent Loop
# ---------------------------------------------------------------------------

def dom_agent_fill(
    page: Page,
    apply_url: str,
    resume_path: str,
    profile: dict,
    max_steps: int = 8,
    resume_text: str = "",
    applicant_qa: dict | None = None,
) -> dict:
    """
    Multi-step DOM-aware form filling loop.
    
    Each step: extract DOM → take screenshot → ask Claude CLI → execute actions.
    Includes loop detection and validation error handling.
    
    Returns: {"success": bool, "method": "dom_agent", "error": str|None}
    """
    stuck_count = 0
    prev_state_hash = None
    
    for step in range(max_steps):
        log.info(f"DOM agent step {step + 1}/{max_steps}")
        
        # 1. Extract current DOM state
        elements = extract_dom_state(page)
        if not elements:
            log.warning("No interactive elements found on page")
            return {"success": False, "method": "dom_agent",
                    "error": "No interactive elements found on page"}
        
        # 2. Extract validation errors
        errors = extract_validation_errors(page)
        if errors:
            log.info(f"Validation errors detected: {errors}")
        
        # 3. Loop detection
        state_sig = compute_state_signature(page.url, elements)
        if state_sig == prev_state_hash:
            stuck_count += 1
            if stuck_count >= 2:
                log.warning(f"Form stuck after {step + 1} steps (same state repeated)")
                return {"success": False, "method": "dom_agent",
                        "error": f"Form stuck after {step + 1} steps — no progress being made"}
        else:
            stuck_count = 0
        prev_state_hash = state_sig
        
        # 4. Build prompt and call Claude CLI
        # Note: screenshots are skipped — claude CLI in subprocess mode doesn't
        # support local file uploads. The structured DOM state is sufficient.
        prompt = build_form_prompt(
            elements, profile, resume_path,
            page.url, page.title(), errors, step, max_steps,
            resume_text=resume_text, applicant_qa=applicant_qa,
        )
        
        try:
            raw = call_llm(prompt, system="You are filling a job application form on behalf of the applicant.")
            actions = json.loads(strip_json_fences(raw))
        except json.JSONDecodeError:
            log.warning("LLM returned invalid JSON — retrying with explicit instruction")
            try:
                retry_prompt = prompt + "\n\nIMPORTANT: Your previous response was not valid JSON. Return ONLY a JSON array."
                raw = call_llm(retry_prompt, system="You are filling a job application form on behalf of the applicant.")
                actions = json.loads(strip_json_fences(raw))
            except Exception as exc:
                log.error(f"LLM JSON retry also failed: {exc}")
                return {"success": False, "method": "dom_agent",
                        "error": f"LLM returned invalid JSON: {exc}"}
        except RuntimeError as exc:
            log.error(f"LLM call failed: {exc}")
            return {"success": False, "method": "dom_agent",
                    "error": f"LLM unavailable: {exc}"}
        
        if not isinstance(actions, list):
            log.warning(f"LLM returned non-list: {type(actions)}")
            continue
        
        log.info(f"LLM returned {len(actions)} actions")
        
        # 6. Execute actions
        for action in actions:
            if not isinstance(action, dict):
                continue
                
            if action.get("action") == "done":
                success = action.get("success", False)
                reason = action.get("reason", "")
                log.info(f"Agent done: success={success}, reason={reason}")
                return {"success": bool(success), "method": "dom_agent",
                        "error": reason if not success else None}
            
            execute_action(page, elements, action, resume_path)
            _human_delay(0.3, 0.8)
        
        # 7. Wait for page to settle after actions
        _human_delay(1.5, 3.0)
        
        # 8. Check if we navigated to a success page
        try:
            page_text = page.inner_text("body").lower()
            success_keywords = [
                "application submitted", "thank you for applying",
                "successfully applied", "application received",
                "we'll be in touch", "application complete",
                "thank you for your interest", "application has been received",
                "we have received your application", "your application has been submitted",
                "thanks for applying", "successfully submitted",
            ]
            if any(kw in page_text for kw in success_keywords):
                log.info("Success confirmation detected on page")
                return {"success": True, "method": "dom_agent", "error": None}
        except Exception:
            pass
        
        # Also check URL
        try:
            url_lower = page.url.lower()
            if any(kw in url_lower for kw in ["thank", "success", "confirm", "submitted", "complete"]):
                log.info("Success detected via URL pattern")
                return {"success": True, "method": "dom_agent", "error": None}
        except Exception:
            pass
    
    return {"success": False, "method": "dom_agent",
            "error": f"Could not complete form after {max_steps} steps"}
