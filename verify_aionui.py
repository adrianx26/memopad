import asyncio
import sys
import os

sys.path.insert(0, os.path.abspath("src"))

from memopad.mcp.async_client import get_client
from memopad.mcp.clients import KnowledgeClient, ProjectClient

async def main():
    if sys.platform == "win32":
        import asyncio
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    async with get_client() as client:
        project_client = ProjectClient(client)
        projects = await project_client.list_projects()
        main_project = next((p for p in projects if p.name == "main"), None)
        if not main_project:
            print("Project 'main' not found")
            return

        k_client = KnowledgeClient(client, main_project.external_id)
        
        # Check the new directory
        directory = "github.com/i-office-ai/aion-ui-assimilated"
        print(f"Checking directory: {directory}")
        try:
            # We don't have a list_entities_in_directory in KnowledgeClient?
            # Let's search for entities with that directory prefix.
            # Actually, I'll just try to resolve the expected ones.
            suffixes = ["overview", "agent-profiles", "skills-and-rules", "functional-diagram"]
            for s in suffixes:
                permalink = f"{directory}/{s}"
                try:
                    ext_id = await k_client.resolve_entity(permalink)
                    print(f"[OK] {permalink} -> {ext_id}")
                except Exception as e:
                    print(f"[FAIL] {permalink}: {e}")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
