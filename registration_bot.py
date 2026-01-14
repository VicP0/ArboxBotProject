import os
import asyncio
from playwright.async_api import async_playwright
from dotenv import load_dotenv

load_dotenv()

class ArboxBot:
    def __init__(self):
        self.email = os.getenv("BOT_EMAIL")
        self.password = os.getenv("BOT_PASSWORD")
        self.studio_url = os.getenv("STUDIO_URL")
        self.browser = None
        self.page = None

    async def start_browser(self):
        """Initializes the browser."""
        p = await async_playwright().start()
        self.browser = await p.chromium.launch(headless=False)
        self.page = await self.browser.new_page()

    async def login(self):
        """Handles Arbox login."""
        try:
            print("Logging in to Arbox...")
            await self.page.goto(self.studio_url)
            frame = self.page.frame_locator("iframe").first
            
            await frame.get_by_role("button", name="כניסה").click()
            await frame.get_by_role("button", name="כניסה עם שם משתמש וסיסמא").click()
            await frame.locator('input[type="email"]').fill(self.email)
            await frame.locator('input[type="password"]').fill(self.password)
            await frame.get_by_role("dialog").get_by_role("button", name="כניסה", exact=True).click()
            
            await frame.locator(".date-events-wrapper").first.wait_for(state="visible")
            print("Login successful!")
        except Exception as e:
            print(f"Login failed: {e}")

    async def close(self):
        if self.browser:
            await self.browser.close()