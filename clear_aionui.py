import asyncio
import sys
import os

sys.path.insert(0, os.path.abspath("src"))

from memopad.mcp.async_client import get_client
from memopad.mcp.clients import KnowledgeClient, ProjectClient

async def main():
    async with get_client() as client:
        project_client = ProjectClient(client)
        projects = await project_client.list_projects()
        main_project = next((p for p in projects if p.name == "main"), None)
        if not main_project:
            print("Project 'main' not found")
            return

        k_client = KnowledgeClient(client, main_project.external_id)
        
        # Try to delete both potential versions
        dirs = ["github.com/iOfficeAI/AionUi", "github.com/i-office-ai/aion-ui"]
        for d in dirs:
            print(f"Attempting to delete directory: {d}")
            try:
                result = await k_client.delete_directory(d)
                print(f"Deleted {result.successful_deletes} files from {d}")
            except Exception as e:
                print(f"Failed to delete {d}: {e}")

if __name__ == "__main__":
    asyncio.run(main())
