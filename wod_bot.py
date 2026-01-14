import os
import asyncio
from playwright.async_api import async_playwright
from telegram import Bot
from dotenv import load_dotenv

# Load credentials
load_dotenv()

class WodBot:
    def __init__(self):
        self.token = os.getenv("TELEGRAM_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.site_url = "https://www.crossfitpanda.com/"

    async def fetch_and_send(self):
        """Scrapes the WOD from the website and sends it to Telegram."""
        async with async_playwright() as p:
            # We use headless=True for background running
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            try:
                print("Navigating to Panda site...")
                await page.goto(self.site_url)
                
                # Navigate to daily workout section
                await page.get_by_role("navigation").get_by_role("link", name="האימון היומי").click()
                await page.locator("main a").first.click()
                await page.wait_for_load_state("networkidle")
                
                wod_text = await page.locator("article").first.inner_text()

                if not wod_text:
                    print("No WOD text found on site.")
                    return

               # Formatting message with HTML instead of Markdown
                header = "<b>🏋️‍♂️ CROSSFIT PANDA - DAILY WOD 🏋️‍♂️</b>\n"
                footer = "\n\n<b>💪 !בהצלחה באימון</b>"
                
                # We wrap the wod_text in a way that characters won't break it
                full_message = f"{header}\n{wod_text}{footer}"

                # Send to Telegram
                bot = Bot(token=self.token)
                await bot.send_message(
                    chat_id=self.chat_id, 
                    text=full_message, 
                    parse_mode='HTML'  # <--- Changed from Markdown to HTML
                )
                print("Telegram message sent successfully!")

            except Exception as e:
                print(f"Error in WodBot: {e}")
            finally:
                await browser.close()