"""WOD scraper — fetches the daily workout and sends it to Telegram."""

import logging

from playwright.async_api import async_playwright
from telegram import Bot

import config

logger = logging.getLogger(__name__)


class WodBot:
    async def fetch_and_send(self) -> None:
        """Scrape the WOD from CrossFit Panda and push it to Telegram."""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                logger.info("Navigating to CrossFit Panda site…")
                await page.goto(config.WOD_SITE_URL)

                await page.get_by_role("navigation").get_by_role(
                    "link", name="האימון היומי"
                ).click()
                await page.locator("main a").first.click()
                await page.wait_for_load_state("networkidle")

                wod_text = await page.locator("article").first.inner_text()
                if not wod_text:
                    logger.warning("No WOD text found on site.")
                    return

                header = "<b>🏋️‍♂️ CROSSFIT PANDA - DAILY WOD 🏋️‍♂️</b>\n"
                footer = "\n\n<b>💪 !בהצלחה באימון</b>"
                full_message = f"{header}\n{wod_text}{footer}"

                bot = Bot(token=config.TELEGRAM_TOKEN)
                await bot.send_message(
                    chat_id=config.TELEGRAM_CHAT_ID,
                    text=full_message,
                    parse_mode="HTML",
                )
                logger.info("WOD sent to Telegram.")

            except Exception as exc:
                logger.exception("WodBot failed: %s", exc)
            finally:
                await browser.close()
