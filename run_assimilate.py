import asyncio
import sys
import os

# Ensure src is in python path
sys.path.insert(0, os.path.abspath("src"))

from loguru import logger
from memopad.mcp.tools.assimilate import assimilate

# Configure logger to stdout
logger.remove()
logger.add(sys.stderr, level="INFO")

async def main():
    url = "https://github.com/mdkrush/openclaw-jarvis-memory"
    print(f"Starting assimilation of {url}...")
    try:
        result = await assimilate(url=url, context=None)
        print("\n" + "="*80 + "\n")
        print(result)
        print("\n" + "="*80 + "\n")
    except Exception as e:
        logger.exception("Assimilation failed")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
