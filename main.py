"""
Entry point — choose a run mode via the first CLI argument.

Modes
-----
  wod       Fetch and send today's WOD once, then exit.
            Used by the GitHub Actions daily_wod.yml workflow.

  bot       Start the interactive Telegram bot with long-polling.
            Also runs the Saturday-21:00 automatic registration scheduler.
            This mode runs forever — deploy it on a persistent host (see
            DEPLOYMENT section below).

  bookweek  One-shot: register all next-week classes right now, then exit.
            Useful for manual testing of the registration logic.

DEPLOYMENT NOTES
----------------
The 'bot' mode is a long-running process and cannot run on GitHub Actions
(jobs are killed after 6 hours).  Recommended hosting options:

  1. VPS  (Hetzner CAX11 ~4 €/mo, DigitalOcean $4/mo, Oracle Always-Free)
     - Copy project to the server.
     - Install dependencies: pip install -r requirements.txt
     - Install Playwright browsers: playwright install chromium --with-deps
     - Keep the bot alive with systemd:

       [Unit]
       Description=CrossFit Panda Bot
       After=network.target

       [Service]
       WorkingDirectory=/home/user/ArboxProject
       ExecStart=/home/user/ArboxProject/venv/bin/python main.py bot
       Restart=on-failure
       RestartSec=10

       [Install]
       WantedBy=multi-user.target

  2. Railway / Fly.io / Render (free tiers available)
     - Add a Dockerfile or Procfile: web: python main.py bot
     - Set env vars in the platform dashboard (not in a committed .env file).
     - Note: Playwright needs --with-deps on Linux; add RUN playwright install
       chromium --with-deps to your Dockerfile.

  3. Keep GitHub Actions for the daily WOD only (daily_wod.yml unchanged).
     Run the interactive bot separately on any always-on machine.

Usage
-----
  python main.py wod
  python main.py bot
  python main.py bookweek
"""

import asyncio
import sys


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return

    mode = sys.argv[1].lower()

    if mode == "wod":
        from wod_bot import WodBot
        asyncio.run(WodBot().fetch_and_send())

    elif mode == "bot":
        # run_bot() manages its own event loop via Application.run_polling().
        from telegram_bot import run_bot
        run_bot()

    elif mode == "bookweek":
        async def _run():
            from arbox_actions import batch_register_next_week
            from browser_session import arbox_page
            async with arbox_page() as page:
                results = await batch_register_next_week(page)
            for msg in results:
                print(msg)

        asyncio.run(_run())

    else:
        print(f"Unknown mode: {mode!r}")
        print("Available modes: wod | bot | bookweek")


if __name__ == "__main__":
    main()
