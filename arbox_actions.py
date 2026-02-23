"""
High-level Arbox scheduling actions.

All public functions receive a Playwright Page that is already authenticated
(from browser_session.arbox_page()) and has the schedule iframe loaded.

Confirmed DOM structure (from live page inspection)
----------------------------------------------------
Frame            : first page.frames[] entry whose URL contains "arboxapp.com"
                   (page.frame_locator("iframe").first does NOT work reliably)

Slot card class  : .session-wrapper
Slot text format : "07:00 - 08:00\\nCrossFit WOD\\nסתיו צרפתי\\n15/20"
                   i.e. start-end time, newline, class name, newline, trainer, newline, N/M
Day grouping     : .date-events-wrapper — one per day, ordered Sunday … Saturday
Full class       : taken >= total in "N/M" pattern; e.g. "20/20" or "5/5"

Week navigation  : frame.locator("svg").nth(2)  → next week
                   frame.locator("svg").nth(1)  → previous week

Register flow    : slot.click()  →  button "רישום".click()
Cancel flow      : slot.click()  →  button "ביטול הרשמה".click()
                                 →  button "כן, לבטל בבקשה".click()

Already-registered detection: after slot.click(), if "ביטול הרשמה" appears
                               instead of "רישום" the user is already booked.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timedelta

import pytz

import config

logger = logging.getLogger(__name__)
TZ = pytz.timezone(config.TIMEZONE)

_DAY_NAME_TO_WEEKDAY: dict[str, int] = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

# Arbox column header abbreviations (confirmed: Friday = "ו׳")
_HEBREW_DAY_ABBREV: dict[int, str] = {
    0: "ב׳",  # Monday
    1: "ג׳",  # Tuesday
    2: "ד׳",  # Wednesday
    3: "ה׳",  # Thursday
    4: "ו׳",  # Friday
    5: "ש׳",  # Saturday
    6: "א׳",  # Sunday
}


def next_weekday(day_name: str, reference: date | None = None) -> date:
    """Return the next occurrence of day_name on or after reference (default: today)."""
    if reference is None:
        reference = datetime.now(TZ).date()
    target_wd = _DAY_NAME_TO_WEEKDAY[day_name.lower()]
    delta = (target_wd - reference.weekday()) % 7
    return reference + timedelta(days=delta)


def _day_header(target: date) -> str:
    """
    Build the Arbox column header text for a given date.
    e.g. date(2025, 2, 27) on a Friday → "27ו׳"
    """
    return f"{target.day}{_HEBREW_DAY_ABBREV[target.weekday()]}"


def _is_full(slot_text: str) -> bool:
    """
    Return True if the slot is full.
    Slot text contains "{taken}/{total}" — full when taken >= total.
    e.g. "20/20" or "20/20(9)" → full.  "4/20" → not full.
    """
    m = re.search(r"(\d+)/(\d+)", slot_text)
    if m:
        return int(m.group(1)) >= int(m.group(2))
    return False


def _get_frame(page):
    """
    Return the main arboxapp.com schedule frame.

    page.frame_locator("iframe").first does not reliably target the schedule
    frame — there are multiple iframes and the schedule is not always first.
    Instead we search page.frames for the first arboxapp.com entry.
    """
    for f in page.frames[1:]:   # frames[0] is the host page itself
        if "arboxapp.com" in f.url:
            return f
    return page.frame_locator("iframe").first   # fallback


# ── Week navigation ───────────────────────────────────────────────────────────

async def _navigate_to_week_of(page, target: date) -> None:
    """
    Click the next/previous SVG arrow until the week containing *target* is shown.

    svg.nth(2) = next-week  |  svg.nth(1) = previous-week  (from recording)
    """
    frame = _get_frame(page)

    def _week_start(d: date) -> date:
        return d - timedelta(days=(d.weekday() + 1) % 7)

    today = datetime.now(TZ).date()
    weeks = (_week_start(target) - _week_start(today)).days // 7

    for _ in range(weeks):
        await frame.locator("svg").nth(2).click()
        await asyncio.sleep(1.5)
    for _ in range(-weeks):
        await frame.locator("svg").nth(1).click()
        await asyncio.sleep(1.5)


# ── Slot finder ───────────────────────────────────────────────────────────────

async def _find_class_slot(page, target: date, time_str: str):
    """
    Return the Locator for the session-wrapper slot on *target* at *time_str*.

    Strategy:
      1. Navigate to the correct week.
      2. Select the .date-events-wrapper at the Sunday-first column index.
         Index formula: (target.weekday() + 1) % 7  (Mon=1, Fri=5, Sun=0)
      3. Inside it, filter .session-wrapper by "{time_str} -" (start-time)
         and optionally by config.CLASS_NAME to avoid picking Open GYM.

    Raises ValueError if no matching slot is found.
    """
    await _navigate_to_week_of(page, target)
    await asyncio.sleep(1.5)   # let the view re-render after navigation

    frame = _get_frame(page)

    # Arbox week starts on Sunday (index 0).
    # Python weekday(): Mon=0, Tue=1, …, Sun=6
    # → Sunday-first index = (weekday + 1) % 7
    col_idx = (target.weekday() + 1) % 7

    wrapper_count = await frame.locator(".date-events-wrapper").count()
    if wrapper_count == 0:
        raise ValueError(
            "No .date-events-wrapper found — the schedule iframe may not have "
            "loaded yet.  Try running with arbox_page(headless=False)."
        )
    if col_idx >= wrapper_count:
        raise ValueError(
            f"Day column {col_idx} not found (only {wrapper_count} visible). "
            f"Target: {target.strftime('%A %d/%m')}."
        )

    day_wrapper = frame.locator(".date-events-wrapper").nth(col_idx)

    # Filter by "{time_str} -" so we match the START time only.
    # e.g. "08:00 -" matches "08:00 - 09:00" but not "07:00 - 08:00".
    start_filter = f"{time_str} -"

    # Primary: start-time + class name (avoids picking Open GYM by mistake)
    slot = (
        day_wrapper.locator(".session-wrapper")
        .filter(has_text=start_filter)
        .filter(has_text=config.CLASS_NAME)
        .first
    )
    if await slot.count() > 0 and await slot.is_visible():
        return slot

    # Fallback: start-time only (in case CLASS_NAME text differs)
    slot = (
        day_wrapper.locator(".session-wrapper")
        .filter(has_text=start_filter)
        .first
    )
    if await slot.count() > 0 and await slot.is_visible():
        return slot

    raise ValueError(
        f"No class slot found for {target.strftime('%A %d/%m')} at {time_str}.\n"
        f"Day column index: {col_idx} (Sunday=0 … Saturday=6), "
        f"columns visible: {wrapper_count}.\n"
        "Run with arbox_page(headless=False) to inspect the DOM."
    )


# ── Available-slots query ─────────────────────────────────────────────────────

async def get_available_slots(page, target: date) -> list[str]:
    """
    Return all CrossFit WOD class start times on *target* date.
    Used to build the dynamic time-selection keyboard.

    Returns a sorted, deduplicated list of "HH:MM" strings.
    Full classes are included — register_class handles the full-class error.
    """
    await _navigate_to_week_of(page, target)
    await asyncio.sleep(1.5)

    frame = _get_frame(page)
    col_idx = (target.weekday() + 1) % 7

    wrapper_count = await frame.locator(".date-events-wrapper").count()
    if wrapper_count == 0 or col_idx >= wrapper_count:
        return []

    day_wrapper = frame.locator(".date-events-wrapper").nth(col_idx)
    slots = day_wrapper.locator(".session-wrapper").filter(has_text=config.CLASS_NAME)
    slot_count = await slots.count()

    times: set[str] = set()
    for slot_idx in range(slot_count):
        slot = slots.nth(slot_idx)
        slot_text = await slot.inner_text()
        m = re.match(r"(\d{2}:\d{2})", slot_text.strip())
        if m:
            times.add(m.group(1))

    # Each DOM slot contains a full "HH:MM - HH:MM" time range, so re.match
    # always captures the START time.  Using a set removes any exact duplicates
    # from elements that share the same start time; no 60-min heuristic is
    # applied here because consecutive real classes (e.g. 17:00, 18:00, 19:00)
    # would otherwise be incorrectly dropped.
    return sorted(times)


# ── Public action functions ───────────────────────────────────────────────────

async def register_class(page, target: date, time_str: str) -> str:
    """
    Register for the class on *target* at *time_str*.

    Confirmed register flow:
        slot.click()
        frame.get_by_role("button", name="רישום").click()

    Also handles:
      - Full class  (detected from slot text "N/N")
      - Already registered  (detected when "ביטול הרשמה" appears instead of "רישום")
    """
    try:
        slot = await _find_class_slot(page, target, time_str)

        # Pre-click check: is the class full?
        slot_text = await slot.inner_text()
        if _is_full(slot_text):
            m = re.search(r"\d+/\d+", slot_text)
            spots = m.group(0) if m else "?"
            return f"Class is full ({spots}) — {target.strftime('%A %d/%m')} at {time_str}."

        await slot.dispatch_event("click")
        frame = _get_frame(page)

        # Give the modal 3 s to show one of the two buttons
        register_btn = frame.get_by_role("button", name="רישום")
        cancel_btn   = frame.get_by_role("button", name="ביטול הרשמה")

        try:
            await register_btn.wait_for(state="visible", timeout=3_000)
            await register_btn.dispatch_event("click")
            await asyncio.sleep(0.8)
            return f"Registered for {target.strftime('%A %d/%m')} at {time_str}."
        except Exception:
            # If the cancel button appeared instead, user is already registered
            if await cancel_btn.is_visible():
                await page.keyboard.press("Escape")
                return f"Already registered for {target.strftime('%A %d/%m')} at {time_str}."
            raise

    except ValueError as exc:
        return str(exc)
    except Exception as exc:
        logger.exception("register_class failed")
        return f"Error while registering: {exc}"


async def cancel_class(page, target: date, time_str: str) -> str:
    """
    Cancel the registration for the class on *target* at *time_str*.

    Confirmed cancel flow:
        slot.click()
        frame.get_by_role("button", name="ביטול הרשמה").click()
        frame.get_by_role("button", name="כן, לבטל בבקשה").click()
    """
    try:
        slot = await _find_class_slot(page, target, time_str)
        await slot.dispatch_event("click")

        frame = _get_frame(page)
        cancel_btn = frame.get_by_role("button", name="ביטול הרשמה")

        try:
            await cancel_btn.wait_for(state="visible", timeout=3_000)
        except Exception:
            # "ביטול הרשמה" didn't appear → user is not registered for this slot
            await page.keyboard.press("Escape")
            return f"Not registered for {target.strftime('%A %d/%m')} at {time_str} — nothing to cancel."

        await cancel_btn.dispatch_event("click")

        confirm_btn = frame.get_by_role("button", name="כן, לבטל בבקשה")
        await confirm_btn.wait_for(state="visible", timeout=5_000)
        await confirm_btn.dispatch_event("click")

        await asyncio.sleep(0.8)
        return f"Cancelled {target.strftime('%A %d/%m')} at {time_str}."

    except ValueError as exc:
        return str(exc)
    except Exception as exc:
        logger.exception("cancel_class failed")
        return f"Error while cancelling: {exc}"


async def get_registered_classes(page) -> list[dict]:
    """
    Scan ALL class slots for the current week and return only the ones
    the user is registered for.

    Strategy:
      - Explicitly navigates to the current week so the scan always starts
        from the right view regardless of any prior navigation.
      - Iterates every .date-events-wrapper (one per day) and every
        .session-wrapper that contains config.CLASS_NAME.
      - Opens each slot modal and waits (up to 2 s) for "ביטול הרשמה" to
        appear — the confirmed indicator that the user is registered.
      - Closes the modal with Escape before moving to the next slot.

    Returns a list of dicts:  {"date": date, "time": str}
    """
    today = datetime.now(TZ).date()

    # Always start from the current week so prior navigation doesn't skew results.
    await _navigate_to_week_of(page, today)
    await asyncio.sleep(1.5)

    days_since_sunday = (today.weekday() + 1) % 7
    this_sunday = today - timedelta(days=days_since_sunday)

    frame = _get_frame(page)
    registered: list[dict] = []

    wrapper_count = await frame.locator(".date-events-wrapper").count()
    if wrapper_count == 0:
        return []

    for col_idx in range(wrapper_count):
        target_date = this_sunday + timedelta(days=col_idx)

        # Skip days that have already passed — no registrations to show there.
        if target_date < today:
            continue

        day_wrapper = frame.locator(".date-events-wrapper").nth(col_idx)

        slots = day_wrapper.locator(".session-wrapper").filter(has_text=config.CLASS_NAME)
        slot_count = await slots.count()

        for slot_idx in range(slot_count):
            slot = slots.nth(slot_idx)

            # Extract start time from slot text ("07:00 - 08:00\n...")
            slot_text = await slot.inner_text()
            m = re.match(r"(\d{2}:\d{2})", slot_text.strip())
            time_str = m.group(1) if m else "?"

            # Open the slot modal
            await slot.dispatch_event("click")

            # 800 ms is generous for the Arbox modal (typically opens in <400 ms).
            # Cutting this from 2 000 ms saves ~1.2 s on every non-registered slot.
            cancel_btn = frame.get_by_role("button", name="ביטול הרשמה")
            is_registered = False
            try:
                await cancel_btn.wait_for(state="visible", timeout=800)
                is_registered = True
            except Exception:
                pass  # button didn't appear → not registered

            if is_registered:
                registered.append({"date": target_date, "time": time_str})

            await page.keyboard.press("Escape")
            try:
                await cancel_btn.wait_for(state="hidden", timeout=1_000)
            except Exception:
                pass
            await asyncio.sleep(0.15)

    # Each class slot produces two DOM elements: one anchored at the start time
    # and one at the end time (e.g. "08:00" and "09:00" for an 08:00-09:00 class).
    # Both pass the registration check, creating duplicate entries.
    # Fix: drop any entry whose time is exactly 60 minutes after another entry
    # on the same date — keeping only the lower boundary (start time).
    def _mins(t: str) -> int:
        return int(t[:2]) * 60 + int(t[3:])

    times_per_day: dict[date, set[str]] = {}
    for item in registered:
        times_per_day.setdefault(item["date"], set()).add(item["time"])

    return [
        item for item in registered
        if not any(
            _mins(earlier) == _mins(item["time"]) - 60
            for earlier in times_per_day[item["date"]]
        )
    ]


async def batch_register_next_week(page) -> list[str]:
    """
    Register for every class in config.WEEKLY_CLASSES for the coming week.
    Called by the Saturday 21:00 scheduler and by /bookweek.
    """
    results: list[str] = []

    today = datetime.now(TZ).date()
    days_until_sunday = (6 - today.weekday()) % 7 or 7
    next_sunday = today + timedelta(days=days_until_sunday)

    for cls in config.WEEKLY_CLASSES:
        target = next_weekday(cls["day"], reference=next_sunday)
        msg = await register_class(page, target, cls["time"])
        logger.info(msg)
        results.append(msg)
        await asyncio.sleep(1.0)

    return results
