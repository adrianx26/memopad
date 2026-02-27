#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run assimilation with UTF-8 encoding."""
import asyncio
import sys
import io

# Set UTF-8 encoding for stdout and stderr
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
sys.path.insert(0, 'src')

async def run_assimilation():
    try:
        from memopad.mcp.tools.assimilate import assimilate
        result = await assimilate('https://github.com/modelcontextprotocol/servers/tree/main/src/memory', project='main')
        print(result)
    except Exception as e:
        print(f'Error: {type(e).__name__}: {e}')
        import traceback
        print(f'Traceback: {traceback.format_exc()}')

if __name__ == "__main__":
    asyncio.run(run_assimilation())
