import asyncio
import sys
import os

# Ensure src is in python path
sys.path.insert(0, os.path.abspath("src"))

from memopad.mcp.async_client import get_client

async def main():
    async with get_client() as client:
        project = "main"
        print(f"Listing notes in project '{project}'...")
        
        try:
            # Use the search endpoint which we know exists from the API routes
            # We can search for everything or a specific tag
            response = await client.get("/search", params={"q": "KittenTTS", "project": project})
            if response.status_code != 200:
                print(f"Search failed: {response.status_code} {response.text}")
                return
                
            results = response.json()
            if not results:
                print("No notes found for KittenTTS.")
                return
                
            print(f"Found {len(results)} matching notes:")
            for res in results:
                print(f"- {res.get('title')} ({res.get('permalink')})")
        except Exception as e:
            print(f"Error checking notes: {e}")

if __name__ == "__main__":
    asyncio.run(main())
