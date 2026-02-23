"""
Playwright session manager for the Arbox portal.

Login flow (confirmed from Playwright recording):
  1. Go to STUDIO_URL (crossfitpanda.com/sirkin)
  2. Click "מערכת שעות" link to open the schedule iframe
  3. frame: click "כניסה"
  4. frame: click "כניסה עם שם משתמש וסיסמה"
  5. frame: fill input[type="email"]  + Tab
  6. frame: fill input[type="password"]
  7. frame: click dialog's "כניסה" (exact=True)

Session validity:
  The "כניסה" button appears when NOT logged in.
  If it is absent after navigating to the schedule → session is still valid.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from playwright.async_api import async_playwright, BrowserContext, Page

import config

SESSION_FILE = Path("arbox_session.json")
logger = logging.getLogger(__name__)

# Large viewport so the Arbox login modal is never clipped outside the screen.
_VIEWPORT = {"width": 1440, "height": 900}


async def _navigate_to_schedule(page: Page) -> None:
    """
    Navigate to the schedule following the exact path from the Playwright recording:
      1. Main site
      2. Click "סניף סירקין" in the navigation bar
      3. Click "מערכת שעות" link
    """
    await page.goto("https://www.crossfitpanda.com/", wait_until="load")
    await page.get_by_role("navigation").get_by_role("link", name="סניף סירקין").click()
    await page.wait_for_load_state("load")
    await page.get_by_role("link", name="מערכת שעות").click()
    await page.wait_for_load_state("load")


async def _do_login(page: Page) -> None:
    """
    Full login sequence — exact match of the recorded Playwright script:

      frame.get_by_role("button", name="כניסה").click()
      frame.get_by_role("button", name="כניסה עם שם משתמש וסיסמה").click()
      frame.locator('input[type="email"]').fill(email) + Tab
      frame.locator('input[type="password"]').fill(password)
      frame.get_by_role("dialog").get_by_role("button", name="כניסה", exact=True).click()

    Credentials come from config.BOT_EMAIL and config.BOT_PASSWORD (.env).
    All clicks use force=True as a fallback in case any element is still
    slightly outside the viewport during animation.
    """
    logger.info("Performing full Arbox login…")
    await _navigate_to_schedule(page)

    frame = page.frame_locator("iframe").first

    # dispatch_event("click") injects the event directly into the DOM —
    # it bypasses all Playwright coordinate / viewport checks entirely.
    # This is necessary because the Arbox login modal can render outside
    # the iframe's visible bounds regardless of the page viewport size.

    # Step 1 — open the login form
    await frame.get_by_role("button", name="כניסה").dispatch_event("click")
    await frame.get_by_role("button", name="כניסה עם שם משתמש וסיסמה").dispatch_event("click")

    # Step 2 — fill credentials (Tab after email activates the password field)
    await frame.locator('input[type="email"]').dispatch_event("click")
    await frame.locator('input[type="email"]').fill(config.BOT_EMAIL)
    await frame.locator('input[type="email"]').press("Tab")
    await frame.locator('input[type="password"]').fill(config.BOT_PASSWORD)

    # Step 3 — submit
    await frame.get_by_role("dialog").get_by_role(
        "button", name="כניסה", exact=True
    ).dispatch_event("click")

    # Wait until the main login button disappears (= login succeeded).
    # Use .first to avoid strict-mode violation: after the dialog opens,
    # multiple elements share the role+name "כניסה" (main btn, dialog btn, code btn).
    await frame.get_by_role("button", name="כניסה").first.wait_for(
        state="hidden", timeout=15_000
    )
    logger.info("Login successful.")


async def _session_is_valid(context: BrowserContext) -> bool:
    """
    Return True if the cached session is still authenticated.
    Logic: navigate to the schedule and check whether the "כניסה" button
    is absent.  If it is gone the user is already logged in.
    """
    page = await context.new_page()
    try:
        await _navigate_to_schedule(page)
        await page.wait_for_timeout(5_000)   # wait for arboxapp.com iframe to load

        # Find the arboxapp.com frame directly — frame_locator("iframe").first
        # is unreliable when multiple iframes are present on the page.
        arbox_frame = None
        for f in page.frames[1:]:
            if "arboxapp.com" in f.url:
                arbox_frame = f
                break

        if arbox_frame is None:
            return False

        login_btn_visible = await arbox_frame.get_by_role("button", name="כניסה").first.is_visible()
        return not login_btn_visible
    except Exception:
        return False
    finally:
        await page.close()


# ── Public context manager ────────────────────────────────────────────────────

@asynccontextmanager
async def arbox_page(headless: bool = True):
    """
    Async context manager — yields an authenticated Playwright Page.

    The page is positioned at STUDIO_URL with the schedule iframe loaded.
    Pass headless=False to watch the browser (useful when debugging selectors).

    Usage:
        async with arbox_page() as page:
            frame = page.frame_locator("iframe").first
            ...
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context: BrowserContext | None = None

        # ── 1. Try cached session ──────────────────────────────────────────
        if SESSION_FILE.exists():
            logger.info("Trying cached Arbox session…")
            context = await browser.new_context(
                storage_state=str(SESSION_FILE),
                viewport=_VIEWPORT,
            )
            if not await _session_is_valid(context):
                logger.info("Cached session expired — will re-login.")
                await context.close()
                context = None

        # ── 2. Fresh login if needed ───────────────────────────────────────
        if context is None:
            context = await browser.new_context(viewport=_VIEWPORT)
            page = await context.new_page()
            await _do_login(page)
            await page.wait_for_timeout(3_000)   # let schedule iframe fully render
            await context.storage_state(path=str(SESSION_FILE))
            logger.info("Session cached at %s", SESSION_FILE)
        else:
            page = await context.new_page()
            await _navigate_to_schedule(page)
            await page.wait_for_timeout(3_000)   # let schedule iframe fully render

        # ── 3. Yield the ready page ────────────────────────────────────────
        try:
            yield page
        finally:
            try:
                await context.storage_state(path=str(SESSION_FILE))
            except Exception:
                pass
            await browser.close()
