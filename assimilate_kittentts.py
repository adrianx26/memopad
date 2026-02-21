import asyncio
import sys
import os

# On Windows, set event loop policy BEFORE asyncio.run() creates the loop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Ensure src is in python path
sys.path.insert(0, os.path.abspath("src"))

from loguru import logger
from memopad.mcp.tools.assimilate import _assimilate_impl as assimilate

# Configure logger to stdout
logger.remove()
logger.add(sys.stderr, level="INFO")

async def main():
    url = "https://github.com/KittenML/KittenTTS"
    try:
        # Context is None as we are running this as a standalone script
        print(f"Starting assimilation of {url}...")
        result = await assimilate(url=url, project="main", context=None)
        print("\n" + "="*80 + "\n")
        print(result)
        print("\n" + "="*80 + "\n")
    except Exception as e:
        logger.exception("Assimilation failed")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())

