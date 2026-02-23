"""
Central configuration.  Every other module imports from here.
Update WEEKLY_CLASSES to match the classes you want booked each week.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "0"))

# ── Arbox credentials ─────────────────────────────────────────────────────────
BOT_EMAIL    = os.getenv("BOT_EMAIL")
BOT_PASSWORD = os.getenv("BOT_PASSWORD")
STUDIO_URL   = os.getenv("STUDIO_URL")   # e.g. https://www.crossfitpanda.com/sirkin

# ── WOD scraper ───────────────────────────────────────────────────────────────
WOD_SITE_URL = "https://www.crossfitpanda.com/"

# ── Timezone ──────────────────────────────────────────────────────────────────
TIMEZONE = "Asia/Jerusalem"

# ── Class name filter ─────────────────────────────────────────────────────────
# Some time slots have multiple concurrent classes (e.g. CrossFit WOD + Open GYM).
# This string is used to pick the right one.  Change if your box names it differently.
CLASS_NAME = "CrossFit WOD"

# ── Weekly batch-registration schedule ───────────────────────────────────────
# Triggered every Saturday at 21:00 (Israel time).
# WODs run every day: 07:00, 08:00, 17:00, 18:00, 19:00, 20:00.
# Fill in only the ones YOU want booked.
# day  : "sunday" | "monday" | "tuesday" | "wednesday" | "thursday" | "friday" | "saturday"
# time : "HH:MM"  in 24-hour Israel local time
WEEKLY_CLASSES = [
    {"day": "sunday",    "time": "07:00"},
    {"day": "monday",    "time": "07:00"},
    {"day": "tuesday",   "time": "07:00"},
    {"day": "wednesday", "time": "07:00"},
    {"day": "thursday",  "time": "07:00"},
]
