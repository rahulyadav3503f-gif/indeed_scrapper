"""Save an Indeed login once, so the collector can reuse the session.

This is the Indeed counterpart of the reference project's
``save_naukri_login.py``, and it works the same way:

    python3 save_indeed_login.py

A visible Chromium window opens on the Indeed home page.  **You** sign in by
hand -- your email, your password, your OTP, and any verification Indeed
asks for.  When the page shows you are signed in, press Enter in this
terminal and the browser session is written to ``indeed_auth.json``.

WHAT THIS SCRIPT DOES NOT DO
----------------------------
It never types, reads or stores a credential: there is no password field
handling in this file at all.  It only asks Playwright to save the session
cookies the browser already holds after YOU signed in.  It does not solve
CAPTCHAs or verification steps for you -- if Indeed asks for one, you answer
it in the window.

IS THE LOGIN REQUIRED?
----------------------
No.  Indeed's search and job pages can be read signed out, and the collector
runs fine without this file -- it prints a one-line warning and carries on.
A signed-in session mainly makes the "Apply" destination visible on more
jobs, which is what ``apply_url`` needs.

The file lands next to this script, is listed in ``.gitignore``, and holds a
live session: treat it like a password, keep it on this machine, and run
``chmod 600 indeed_auth.json`` after creating it.
"""

import asyncio
import os
import sys
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except ImportError:                                  # pragma: no cover
    sys.exit("Playwright is not installed.  Run:\n"
             "    pip install -r requirements.txt\n"
             "    python3 -m playwright install chromium")

BASE_DIR = Path(__file__).resolve().parent

# Kept in step with find_indeed_job.py.  Those two names are the only
# settings this script has, and they are read from the main file so there is
# never a second copy to update.
AUTH_FILE = BASE_DIR / "indeed_auth.json"
HOME_URL = "https://in.indeed.com/"
WINDOW = {"width": 1366, "height": 768}

try:
    sys.path.insert(0, str(BASE_DIR))
    import find_indeed_job as _main
    AUTH_FILE = Path(_main.project_path(_main.AUTH_FILE))
    HOME_URL = _main.INDEED_BASE_URL.rstrip("/") + "/"
    WINDOW = {"width": _main.BROWSER_WIDTH, "height": _main.BROWSER_HEIGHT}
except Exception as error:                           # pragma: no cover
    print("[WARN] could not read settings from find_indeed_job.py (%s) -- "
          "using the defaults in this file." % error)


async def main() -> int:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        context = await browser.new_context(viewport=WINDOW)
        page = await context.new_page()

        await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)

        # Decline non-essential cookies if the banner is in the way.  Only
        # the decline control is ever clicked.
        for selector in ("#onetrust-reject-all-handler",
                         "button:has-text('Reject All')"):
            try:
                locator = page.locator(selector)
                if await locator.count() and await locator.first.is_visible():
                    await locator.first.click(timeout=3000)
                    break
            except Exception:
                continue

        print("\n" + "=" * 68)
        print("Indeed browser opened: %s" % HOME_URL)
        print("=" * 68)
        print("1. Click 'Sign in' and log in MANUALLY -- your email, your")
        print("   password, your OTP, and any verification Indeed asks for.")
        print("2. Wait until the page clearly shows you are signed in.")
        print("3. Come back here and press Enter.")
        print("\nNothing is typed for you and no credential is stored by this")
        print("script -- only the session the browser already holds.\n")

        await asyncio.to_thread(
            input, "press Enter in this terminal to save the login: ")

        await context.storage_state(path=str(AUTH_FILE))

        try:
            os.chmod(AUTH_FILE, 0o600)
        except OSError:
            pass

        print("\nLogin session saved: %s" % AUTH_FILE)
        print("It holds a live session -- keep it on this machine. It is")
        print("already listed in .gitignore.")
        print("\nNow run the collector:")
        print("    python3 find_indeed_job.py")

        await context.close()
        await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
