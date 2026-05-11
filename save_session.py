"""
save_session.py
───────────────
One-time utility to save a valid LinkedIn session for use by the pipeline.

Usage:
    python save_session.py

Opens a headed browser with the persistent Chrome profile, logs in with
credentials from .env, waits for you to complete 2FA if prompted.
The session is saved automatically in the browser profile — subsequent
pipeline runs reuse it without needing to login again.
"""

import sys
import time
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")
sys.path.insert(0, str(BASE_DIR / "scripts"))

import os
from apply_jobs import CHROME_PROFILE_DIR
from playwright.sync_api import sync_playwright

def main():
    email    = os.environ.get("LINKEDIN_EMAIL", "").strip()
    password = os.environ.get("LINKEDIN_PASSWORD", "").strip()

    if not email or not password:
        print("ERROR: LINKEDIN_EMAIL and LINKEDIN_PASSWORD must be set in .env")
        sys.exit(1)

    print(f"Logging in as: {email}")
    print("A browser window will open. Complete 2FA if prompted, then wait.")

    CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(CHROME_PROFILE_DIR),
            headless=False,
            slow_mo=50,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/136.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        page = context.pages[0] if context.pages else context.new_page()

        try:
            print("\nNavigating to LinkedIn login...")
            page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=30000)

            page.fill("#username", email)
            page.fill("#password", password)
            page.click('button[type="submit"]')

            print("\nWaiting for login to complete (up to 60s)...")
            print("Complete any 2FA/verification in the browser window.")

            # Wait until we land on any post-login page (not login/checkpoint)
            for _ in range(300):
                url = page.url
                if not any(x in url for x in ["login", "checkpoint", "challenge", "authwall"]):
                    break
                if any(x in url for x in ["checkpoint", "challenge"]):
                    print(f"  -> Verification required — complete it in the browser window...")
                time.sleep(1)
            else:
                print("WARNING: Timed out waiting for login — saving whatever session exists")

            # Final check
            if "/feed" in page.url or page.locator(".global-nav__me").count() > 0:
                print("\nOK Logged in successfully!")
            else:
                print(f"\nWARNING:  Not sure if logged in (url={page.url}) — session saved anyway")

            print(f"OK Session saved to browser profile: {CHROME_PROFILE_DIR}")
            print("\nYou can now run: python main_pipeline.py")
        finally:
            context.close()

if __name__ == "__main__":
    main()
