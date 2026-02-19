#!/usr/bin/env python
"""Script to run the assimilate tool on a GitHub repository."""

import asyncio
import sys

from memopad.mcp.tools.assimilate import assimilate


async def main():
    """Run assimilation on the specified URL."""
    url = "https://github.com/sipeed/picoclaw"

    print(f"Starting assimilation of {url}...")
    # Call the underlying function if it is wrapped in a FunctionTool
    if hasattr(assimilate, "fn"):
        result = await assimilate.fn(url=url, project="main", max_depth=10, max_pages=1000)
    else:
        result = await assimilate(url=url, project="main", max_depth=10, max_pages=1000)
    
    print("\n" + "="*60)
    print("ASSIMILATION RESULT")
    print("="*60)
    print(result)
    
    return result


if __name__ == "__main__":
    asyncio.run(main())
