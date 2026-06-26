import asyncio
import sys
import os

sys.path.insert(0, os.path.abspath("src"))

from memopad.mcp.async_client import get_client
from memopad.mcp.project_context import get_active_project
from memopad.repository.entity_repository import EntityRepository
from memopad import db

async def main():
    async with get_client() as client:
        active_project = await get_active_project(client, "main")
        print(f"Project: {active_project.name} (id={active_project.id})")
        
        # We need to initialize the DB engine if it's not already
        from memopad.services.initialization import initialize_app
        from memopad.config import ConfigManager
        config = ConfigManager().config
        await initialize_app(config)
        
        repo = EntityRepository(db._session_maker, active_project.id)
        
        # 1. Search by title
        print("\nSearching by title 'Overview'...")
        entities = await repo.get_by_title("Overview")
        for e in entities:
            print(f"- id={e.id}, external_id={e.external_id}, permalink={e.permalink}, directory={e.directory}, file_path={e.file_path}")
            
        # 2. Search by directory
        print("\nSearching by directory prefix 'github.com/i'...")
        entities = await repo.find_by_directory_prefix("github.com/i")
        for e in entities:
            print(f"- id={e.id}, title={e.title}, permalink={e.permalink}, directory={e.directory}")

if __name__ == "__main__":
    asyncio.run(main())
