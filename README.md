Arbox & WOD Automation Bot 🏋️‍♂️🤖
A Python-based automation tool designed for CrossFit athletes. This project automates two main tasks: scraping the Daily Workout (WOD) and delivering it via Telegram, and managing gym session registrations via the Arbox platform.

🚀 Features
Daily WOD Scraper: Automatically navigates to the gym's website, extracts the daily workout, and sends a formatted message to a Telegram chat.

GitHub Actions Integration: The bot runs entirely in the cloud. No need to keep your computer on; it triggers every morning at 06:30 AM (Israel Time).

Arbox Manager (In Progress): A dedicated module for automated login and registration for gym sessions using Playwright.

Secure Credentials: Uses GitHub Secrets to handle sensitive data like Telegram tokens and login credentials.

🛠 Tech Stack
Language: Python 3.10+

Automation: Playwright (Headless Browser)

Messaging: python-telegram-bot

CI/CD: GitHub Actions

📁 Project Structure
Plaintext

├── .github/workflows/
│ └── daily_wod.yml # GitHub Actions schedule configuration
├── main.py # Main entry point (CLI dispatcher)
├── wod_bot.py # Logic for scraping and Telegram messaging
├── registration_bot.py # Logic for Arbox interaction
├── requirements.txt # Project dependencies
└── .gitignore # Files to be ignored by Git (venv, .env, etc.)
⚙️ Setup & Installation
Clone the repository:

Bash

git clone https://github.com/VicP0/ArboxBotProject.git
cd ArboxBotProject
Install dependencies:

Bash

pip install -r requirements.txt
playwright install chromium --with-deps
Environment Variables: Create a .env file for local testing:

קטע קוד

TELEGRAM_TOKEN=your_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
STUDIO_URL=your_arbox_studio_url
BOT_EMAIL=your_email
BOT_PASSWORD=your_password
🤖 GitHub Actions Configuration
To run this bot automatically in the cloud:

Go to your GitHub Repository Settings > Secrets and variables > Actions.

Add the following secrets:

TELEGRAM_TOKEN

TELEGRAM_CHAT_ID

The bot is scheduled to run daily at 04:30 UTC (06:30 AM IST).

📝 License
This project is for personal use and educational purposes.
