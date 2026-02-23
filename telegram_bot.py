"""
Telegram bot — long-polling, menu keyboards, and APScheduler integration.

Interaction modes
-----------------
1. Tap-menu  : A persistent reply keyboard lives at the bottom of the chat.
               Tapping a button opens an inline keyboard for day → time selection.
2. Commands  : /wod /register /cancel /bookweek still work for power-users.

Scheduling
----------
Every Saturday at 21:00 Israel time the bot automatically registers you for
all classes listed in config.WEEKLY_CLASSES.

Running
-------
    python main.py bot
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timedelta

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
from arbox_actions import (
    batch_register_next_week,
    cancel_class,
    get_available_slots,
    get_registered_classes,
    register_class,
)
from browser_session import arbox_page
from queue_manager import add_to_queue, clear_queue, get_queue
from wod_bot import WodBot

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
TZ = pytz.timezone(config.TIMEZONE)


def _this_saturday() -> date:
    """Return the date of this week's Saturday (Arbox week boundary)."""
    today = datetime.now(TZ).date()
    return today + timedelta(days=(5 - today.weekday()) % 7)

# ── Persistent reply keyboard (always visible at the bottom) ──────────────────

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["🏋️ WOD היומי"],
        ["📅 רישום לשיעור", "❌ ביטול שיעור"],
        ["📊 השיעורים שלי", "📝 רישומים עתידיים"],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

# ── Inline keyboard builders ──────────────────────────────────────────────────

# (Hebrew label, callback-data day value)
_WEEK_DAYS = [
    ("ראשון", "sunday"),
    ("שני",   "monday"),
    ("שלישי", "tuesday"),
    ("רביעי", "wednesday"),
    ("חמישי", "thursday"),
    ("שישי",  "friday"),
    ("שבת",   "saturday"),
]

def _days_keyboard(action: str) -> InlineKeyboardMarkup:
    """
    Day-selection inline keyboard.
    action = "reg" (register) or "can" (cancel)
    Callback format: "{action}|day|{day_value}"
    """
    rows = [
        # Shortcuts: today / tomorrow
        [
            InlineKeyboardButton("היום",  callback_data=f"{action}|day|today"),
            InlineKeyboardButton("מחר",   callback_data=f"{action}|day|tomorrow"),
        ]
    ]
    # Full week in pairs
    for i in range(0, len(_WEEK_DAYS), 2):
        pair = _WEEK_DAYS[i:i + 2]
        rows.append([
            InlineKeyboardButton(heb, callback_data=f"{action}|day|{eng}")
            for heb, eng in pair
        ])
    rows.append([InlineKeyboardButton("✖ סגור", callback_data="dismiss")])
    return InlineKeyboardMarkup(rows)


def _dynamic_times_keyboard(action: str, day: str, times: list[str]) -> InlineKeyboardMarkup:
    """
    Time-selection inline keyboard built from the live Arbox schedule.
    Callback format: "{action}|{day_value}|{HH:MM}"
    """
    rows = []
    for i in range(0, len(times), 3):
        rows.append([
            InlineKeyboardButton(t, callback_data=f"{action}|{day}|{t}")
            for t in times[i:i + 3]
        ])
    rows.append([
        InlineKeyboardButton("⬅️ חזרה", callback_data=f"back|{action}"),
        InlineKeyboardButton("✖ סגור",  callback_data="dismiss"),
    ])
    return InlineKeyboardMarkup(rows)


# ── Date / time parsing ───────────────────────────────────────────────────────

_DAY_NAME_TO_WEEKDAY: dict[str, int] = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    "שני": 0, "שלישי": 1, "רביעי": 2, "חמישי": 3,
    "שישי": 4, "שבת": 5, "ראשון": 6,
}


def _parse_time(token: str) -> str | None:
    t = token.strip().lower()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(am|pm)?", t)
    if m:
        h, mn, mer = int(m.group(1)), int(m.group(2)), m.group(3)
        if mer == "pm" and h < 12:
            h += 12
        elif mer == "am" and h == 12:
            h = 0
        return f"{h:02d}:{mn:02d}"
    m = re.fullmatch(r"(\d{1,2})(am|pm)", t)
    if m:
        h, mer = int(m.group(1)), m.group(2)
        if mer == "pm" and h < 12:
            h += 12
        elif mer == "am" and h == 12:
            h = 0
        return f"{h:02d}:00"
    m = re.fullmatch(r"(\d{3,4})", t)
    if m:
        raw = m.group(1).zfill(4)
        return f"{raw[:2]}:{raw[2:]}"
    return None


def _parse_day(token: str) -> date | None:
    t = token.strip().lower()
    today = datetime.now(TZ).date()
    if t in ("today", "היום"):
        return today
    if t in ("tomorrow", "מחר"):
        return today + timedelta(days=1)
    if t in _DAY_NAME_TO_WEEKDAY:
        wd = _DAY_NAME_TO_WEEKDAY[t]
        delta = (wd - today.weekday()) % 7
        if delta == 0:
            delta = 7
        return today + timedelta(days=delta)
    return None


def _parse_class_args(args: list[str]) -> tuple[date, str] | None:
    if len(args) < 2:
        return None
    target_date = _parse_day(args[0])
    time_str = _parse_time(args[1])
    if target_date is None or time_str is None:
        return None
    return target_date, time_str


# ── Inline keyboard callback handler ─────────────────────────────────────────

async def handle_callback(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle all inline button taps.

    Callback data format:
      "dismiss"              → close the inline message
      "back|reg"             → go back to day selection for register
      "back|can"             → go back to day selection for cancel
      "{action}|day|{day}"  → day chosen, show time keyboard
      "{action}|{day}|{t}"  → time chosen, execute the action
    """
    query = update.callback_query
    await query.answer()

    data = query.data
    parts = data.split("|")

    # ── Dismiss ──
    if data == "dismiss":
        await query.edit_message_text("✖")
        return

    # ── Direct cancel (from registered-classes list) ──
    if parts[0] == "cancel_direct":
        target_date = date.fromisoformat(parts[1])
        time_str = parts[2]
        await query.edit_message_text(
            f"🔄 מבטל... {target_date.strftime('%A %d/%m')} {time_str}"
        )
        try:
            async with arbox_page() as page:
                result = await cancel_class(page, target_date, time_str)
            await query.edit_message_text(result)
        except Exception as exc:
            logger.exception("cancel_direct error")
            await query.edit_message_text(f"שגיאה: {exc}")
        return

    # ── Back to day selection ──
    if parts[0] == "back":
        action = parts[1]
        label = "רישום" if action == "reg" else "ביטול"
        await query.edit_message_text(
            f"בחר יום ל{label}:",
            reply_markup=_days_keyboard(action),
        )
        return

    action = parts[0]  # "reg" or "can"

    # ── Day chosen → fetch live slots, show time keyboard ──
    if parts[1] == "day":
        day = parts[2]
        target_date = _parse_day(day)
        if target_date is None:
            await query.edit_message_text("שגיאה: לא הצלחתי לפענח את היום.")
            return
        label = "רישום" if action == "reg" else "ביטול"
        await query.edit_message_text(
            f"🔄 טוען שעות זמינות ל-{target_date.strftime('%A %d/%m')}..."
        )
        try:
            async with arbox_page() as page:
                times = await get_available_slots(page, target_date)
            if not times:
                if action == "reg" and target_date > _this_saturday():
                    # Next week's schedule isn't published yet — use default times.
                    times = ["07:00", "08:00", "09:00", "17:00", "18:00", "19:00", "20:00", "21:00"]
                else:
                    await query.edit_message_text(
                        f"אין שיעורי {config.CLASS_NAME} ב-{target_date.strftime('%A %d/%m')}."
                    )
                    return
            await query.edit_message_text(
                f"בחר שעה — {label} {target_date.strftime('%A %d/%m')}:",
                reply_markup=_dynamic_times_keyboard(action, day, times),
            )
        except Exception as exc:
            logger.exception("get_available_slots error")
            await query.edit_message_text(f"שגיאה בטעינת שעות: {exc}")
        return

    # ── Time chosen → queue (reg) or execute immediately (can) ──
    day, time_str = parts[1], parts[2]
    target_date = _parse_day(day)
    if target_date is None:
        await query.edit_message_text("שגיאה: לא הצלחתי לפענח את היום.")
        return

    if action == "reg":
        if target_date <= _this_saturday():
            # Current week — registration is open now, execute immediately.
            await query.edit_message_text(
                f"🔄 רושם... {target_date.strftime('%A %d/%m')} {time_str}"
            )
            try:
                async with arbox_page() as page:
                    result = await register_class(page, target_date, time_str)
                await query.edit_message_text(result)
            except Exception as exc:
                logger.exception("handle_callback register error")
                await query.edit_message_text(f"שגיאה: {exc}")
        else:
            # Next week — queue for Saturday 21:00 when registration opens.
            added = add_to_queue(target_date, time_str)
            if added:
                await query.edit_message_text(
                    f"✅ הרישום נשמר!\n"
                    f"📅 {target_date.strftime('%A %d/%m')} | {time_str}\n\n"
                    f"הרישום יבוצע אוטומטית ביום שבת ב-21:00."
                )
            else:
                await query.edit_message_text(
                    f"הרישום ל-{target_date.strftime('%A %d/%m')} {time_str} כבר נמצא בתור."
                )
        return

    # cancel — execute immediately
    await query.edit_message_text(
        f"🔄 מבטל... {target_date.strftime('%A %d/%m')} {time_str}"
    )
    try:
        async with arbox_page() as page:
            result = await cancel_class(page, target_date, time_str)
        await query.edit_message_text(result)
    except Exception as exc:
        logger.exception("handle_callback cancel error")
        await query.edit_message_text(f"שגיאה: {exc}")


# ── Reply-keyboard text handler ───────────────────────────────────────────────

async def handle_menu_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle taps on the persistent reply keyboard buttons."""
    text = update.message.text

    if text == "🏋️ WOD היומי":
        await cmd_wod(update, ctx)

    elif text == "📅 רישום לשיעור":
        await update.message.reply_text(
            "בחר יום לרישום:",
            reply_markup=_days_keyboard("reg"),
        )

    elif text == "❌ ביטול שיעור":
        await cmd_cancel_pick(update, ctx)

    elif text == "📊 השיעורים שלי":
        await cmd_mystatus(update, ctx)

    elif text == "📝 רישומים עתידיים":
        await cmd_myqueue(update, ctx)


# ── Command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Shalom! I'm your CrossFit Panda bot 🏋️\n\n"
        "Use the menu below, or type commands directly:\n"
        "  /wod — today's workout\n"
        "  /register <day> <time> — current week: immediate | next week: queued\n"
        "  /cancel monday 08:00 — cancel immediately\n"
        "  /myqueue — show pending next-week registrations\n"
        "  /bookweek — register all next-week classes now\n\n"
        "Current-week classes are registered immediately.\n"
        "Next-week classes are queued and executed every Saturday at 21:00.",
        reply_markup=MAIN_KEYBOARD,
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, ctx)


async def cmd_wod(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("מביא WOD...")
    try:
        await WodBot().fetch_and_send()
        await update.message.reply_text("WOD נשלח!")
    except Exception as exc:
        logger.exception("cmd_wod error")
        await update.message.reply_text(f"שגיאה: {exc}")


async def cmd_register(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    parsed = _parse_class_args(ctx.args or [])
    if parsed is None:
        await update.message.reply_text(
            "שימוש: /register <יום> <שעה>\n"
            "דוגמה: /register sunday 07:00"
        )
        return
    target_date, time_str = parsed
    if target_date <= _this_saturday():
        await update.message.reply_text(
            f"🔄 רושם {target_date.strftime('%A %d/%m')} {time_str}..."
        )
        try:
            async with arbox_page() as page:
                result = await register_class(page, target_date, time_str)
            await update.message.reply_text(result)
        except Exception as exc:
            logger.exception("cmd_register error")
            await update.message.reply_text(f"שגיאה: {exc}")
    else:
        added = add_to_queue(target_date, time_str)
        if added:
            await update.message.reply_text(
                f"✅ הרישום נשמר!\n"
                f"📅 {target_date.strftime('%A %d/%m')} | {time_str}\n\n"
                f"הרישום יבוצע אוטומטית ביום שבת ב-21:00."
            )
        else:
            await update.message.reply_text(
                f"הרישום ל-{target_date.strftime('%A %d/%m')} {time_str} כבר נמצא בתור."
            )


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    parsed = _parse_class_args(ctx.args or [])
    if parsed is None:
        await update.message.reply_text(
            "שימוש: /cancel <יום> <שעה>\n"
            "דוגמה: /cancel sunday 07:00"
        )
        return
    target_date, time_str = parsed
    await update.message.reply_text(f"🔄 מבטל {target_date.strftime('%A %d/%m')} {time_str}...")
    try:
        async with arbox_page() as page:
            result = await cancel_class(page, target_date, time_str)
        await update.message.reply_text(result)
    except Exception as exc:
        logger.exception("cmd_cancel error")
        await update.message.reply_text(f"שגיאה: {exc}")


async def cmd_bookweek(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("🔄 רושם לכל שיעורי השבוע הבא...")
    try:
        async with arbox_page() as page:
            results = await batch_register_next_week(page)
        summary = "\n".join(results) if results else "אין שיעורים מוגדרים ב-config.py."
        await update.message.reply_text(f"סיימתי:\n{summary}")
    except Exception as exc:
        logger.exception("cmd_bookweek error")
        await update.message.reply_text(f"שגיאה: {exc}")


async def cmd_cancel_pick(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show only registered classes as cancel buttons — no day selection needed."""
    await update.message.reply_text("🔄 טוען שיעורים רשומים...")
    try:
        async with arbox_page() as page:
            classes = await get_registered_classes(page)
        if not classes:
            await update.message.reply_text("אין שיעורים רשומים לשבוע הנוכחי.")
            return
        rows = [
            [InlineKeyboardButton(
                f"{c['date'].strftime('%A %d/%m')} | {c['time']}",
                callback_data=f"cancel_direct|{c['date'].isoformat()}|{c['time']}",
            )]
            for c in classes
        ]
        rows.append([InlineKeyboardButton("✖ סגור", callback_data="dismiss")])
        await update.message.reply_text(
            "בחר שיעור לביטול:",
            reply_markup=InlineKeyboardMarkup(rows),
        )
    except Exception as exc:
        logger.exception("cmd_cancel_pick error")
        await update.message.reply_text(f"שגיאה: {exc}")


async def cmd_mystatus(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("🔄 סורק שיעורים רשומים... (זה עלול לקחת דקה)")
    try:
        async with arbox_page() as page:
            classes = await get_registered_classes(page)

        if not classes:
            await update.message.reply_text("אין שיעורים רשומים לשבוע הנוכחי.")
            return

        lines = ["📊 *השיעורים שלי — שבוע נוכחי:*\n"]
        for c in classes:
            date_str = c["date"].strftime("%A %d/%m")
            lines.append(f"✅ {date_str} | {c['time']}")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as exc:
        logger.exception("cmd_mystatus error")
        await update.message.reply_text(f"שגיאה: {exc}")


async def cmd_myqueue(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    queue = get_queue()
    if not queue:
        await update.message.reply_text("התור ריק — אין רישומים ממתינים לשבת.")
        return
    lines = ["📝 *רישומים ממתינים לשבת 21:00:*\n"]
    for item in queue:
        lines.append(f"⏳ {item['date'].strftime('%A %d/%m')} | {item['time']}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── Scheduled job (Saturday 21:00 Israel time) ────────────────────────────────

async def _scheduled_batch_register() -> None:
    logger.info("Scheduled weekly batch registration triggered.")
    bot = Bot(token=config.TELEGRAM_TOKEN)
    all_results: list[str] = []
    try:
        async with arbox_page() as page:
            # 1. Drain the manual queue first
            queued = get_queue()
            if queued:
                logger.info("Draining %d queued registration(s).", len(queued))
                for item in queued:
                    msg = await register_class(page, item["date"], item["time"])
                    logger.info("Queue item result: %s", msg)
                    all_results.append(msg)
                    await asyncio.sleep(1.0)
                clear_queue()

            # 2. Register all WEEKLY_CLASSES for the coming week
            weekly_results = await batch_register_next_week(page)
            all_results.extend(weekly_results)

        summary = "\n".join(all_results) if all_results else "אין שיעורים מוגדרים."
        await bot.send_message(
            chat_id=config.TELEGRAM_CHAT_ID,
            text=f"הרשמה שבועית הושלמה:\n{summary}",
        )
    except Exception as exc:
        logger.exception("Scheduled batch registration failed")
        try:
            await bot.send_message(
                chat_id=config.TELEGRAM_CHAT_ID,
                text=f"הרשמה שבועית נכשלה: {exc}",
            )
        except Exception:
            pass


# ── Main runner ───────────────────────────────────────────────────────────────

def run_bot() -> None:
    scheduler = AsyncIOScheduler(timezone=config.TIMEZONE)
    scheduler.add_job(
        _scheduled_batch_register,
        CronTrigger(day_of_week="sat", hour=21, minute=0, timezone=config.TIMEZONE),
        id="weekly_batch_register",
        replace_existing=True,
    )

    async def on_startup(app: Application) -> None:
        scheduler.start()
        logger.info("Scheduler started — next batch registration: Saturday 21:00 Israel time.")

    async def on_shutdown(app: Application) -> None:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")

    app = (
        Application.builder()
        .token(config.TELEGRAM_TOKEN)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )

    # Command handlers (still work for power-users)
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("help",     cmd_help))
    app.add_handler(CommandHandler("wod",      cmd_wod))
    app.add_handler(CommandHandler("register", cmd_register))
    app.add_handler(CommandHandler("cancel",   cmd_cancel))
    app.add_handler(CommandHandler("bookweek", cmd_bookweek))
    app.add_handler(CommandHandler("mystatus", cmd_mystatus))
    app.add_handler(CommandHandler("myqueue",  cmd_myqueue))

    # Inline keyboard callbacks
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Reply keyboard button taps (text messages that are not commands)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_text))

    logger.info("Starting Telegram bot with long-polling…")
    app.run_polling(allowed_updates=["message", "callback_query"])
