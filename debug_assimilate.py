
import asyncio
import sys
import os
from loguru import logger
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# Add src to sys.path
sys.path.append(os.path.join(os.getcwd(), "src"))

from memopad.mcp.tools.assimilate import crawl, _clone_github_repo, _is_github_repo, _build_overview_note, _build_agent_profiles_note, _build_skills_rules_note, _build_concepts_note, _build_github_links_note

async def run_debug():
    url = "https://github.com/openclaw/openclaw"
    print(f"--- Starting Debug Simulation for {url} ---", file=sys.stderr)
    loop = asyncio.get_running_loop()
    print(f"Loop type: {type(loop)}", file=sys.stderr)
    print(f"Policy: {asyncio.get_event_loop_policy()}", file=sys.stderr)
    sys.stderr.flush()
    
    # Configure logger to print to stdout
    logger.remove()
    logger.add(sys.stdout, level="INFO")

    print("[Step 1] Assimilating...")
    try:
        if _is_github_repo(url):
            print(f"Detected GitHub repo, cloning {url}...")
            data = await _clone_github_repo(url, max_files=10)
        else:
            print(f"Starting generic crawl of {url}...")
            data = await crawl(url, max_depth=2, max_pages=10)
    except Exception as e:
        print(f"Assimilation failed: {e}")
        import traceback
        traceback.print_exc()
        return

    print(f"\n[Step 2] Crawl Complete.")
    print(f"Pages found: {len(data['pages'])}")
    print(f"GitHub links: {len(data['all_github_links'])}")
    print(f"External links: {len(data['all_external_links'])}")
    if data['all_external_links']:
        print("Sample external links:")
        for link in data['all_external_links'][:10]:
            print(f" - {link}")
    print(f"Errors: {len(data['errors'])}")

    print("\n[Step 3] Analyzing Content...")
    for page in data['pages']:
        print(f" - {page['url']}")
        if page['content_types']:
            print(f"   Detected types: {page['content_types']}")
        
    print("\n[Step 4] Simulating Note Generation...")
    
    notes = []
    
    overview = _build_overview_note(url, data)
    notes.append(("Overview", overview))
    
    agent_note = _build_agent_profiles_note(data)
    if agent_note:
        notes.append(("Agent Profiles", agent_note))
        
    skills_note = _build_skills_rules_note(data)
    if skills_note:
        notes.append(("Skills and Rules", skills_note))
        
    concepts_note = _build_concepts_note(data)
    if concepts_note:
        notes.append(("Concepts and Ideas", concepts_note))
        
    github_note = _build_github_links_note(data)
    if github_note:
        notes.append(("GitHub Links Index", github_note))

    print(f"\nWould generate {len(notes)} notes:")
    for title, content in notes:
        print(f"\n--- Note: {title} ---")
        # Print first few lines of content
        print("\n".join(content.split("\n")[:10]))
        print("...")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_debug())
    finally:
        loop.close()
