import asyncio
import sys
from wod_bot import WodBot
from registration_bot import ArboxBot 

async def main():
    if len(sys.argv) > 1 and sys.argv[1] == "wod":
        print("Starting Daily WOD update...")
        bot = WodBot()
        await bot.fetch_and_send() # Now correctly calls without arguments
    
    elif len(sys.argv) > 1 and sys.argv[1] == "book":
        print("Starting Registration process...")
        reg_bot = ArboxBot()
        try:
            await reg_bot.start_browser()
            await reg_bot.login()
            # Future booking logic here
        finally:
            await reg_bot.close()
    
    else:
        print("Please specify action: 'wod' or 'book'")
        print("Example: python main.py wod")

if __name__ == "__main__":
    asyncio.run(main())