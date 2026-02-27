"""Run assimilation directly without MCP layer, with content truncation."""
import asyncio
import sys
sys.path.insert(0, 'src')

MAX_FILE_TEXT = 50_000   # max chars per file to include in a note
MAX_NOTE_CONTENT = 800_000  # max total chars per note (well below 1M limit)

async def run():
    from memopad.mcp.tools.assimilate import _is_github_repo, _clone_github_repo
    from memopad.mcp.tools.assimilate import _build_overview_note, _build_skills_rules_note
    from memopad.mcp.tools.assimilate import _build_agent_profiles_note, _build_tools_functions_note
    from memopad.mcp.tools.assimilate import _build_concepts_note, _build_functional_diagram_note
    from memopad.mcp.async_client import get_client
    from memopad.mcp.project_context import get_active_project
    from memopad.mcp.clients import KnowledgeClient
    from memopad.schemas.base import Entity
    from urllib.parse import urlparse

    url = 'https://github.com/modelcontextprotocol/servers'
    project = 'imported'

    print(f'Is github repo: {_is_github_repo(url)}')
    print('Cloning...')
    data = await _clone_github_repo(url)
    print(f'Pages found: {len(data["pages"])}')
    print(f'Errors: {data["errors"]}')

    # Truncate individual file text to prevent oversized notes
    for p in data['pages']:
        if len(p['text']) > MAX_FILE_TEXT:
            p['text'] = p['text'][:MAX_FILE_TEXT] + '\n\n[... content truncated ...]'

    for p in data['pages'][:15]:
        print(f'  - {p["url"]} ({p["content_types"]})')

    if not data['pages']:
        print('No pages found. Aborting.')
        return

    # Build notes
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    if domain.startswith('www.'):
        domain = domain[4:]
    path_parts = parsed.path.strip('/').split('/')
    if len(path_parts) >= 2:
        domain = f"{domain}/{path_parts[0]}/{path_parts[1]}"

    def safe_truncate(content):
        if content and len(content) > MAX_NOTE_CONTENT:
            return content[:MAX_NOTE_CONTENT] + '\n\n[... note truncated to fit size limit ...]'
        return content

    notes_to_write = []

    overview = safe_truncate(_build_overview_note(url, data))
    notes_to_write.append(('Overview', overview))

    agent_note = safe_truncate(_build_agent_profiles_note(data))
    if agent_note:
        notes_to_write.append(('Agent Profiles', agent_note))

    skills_note = safe_truncate(_build_skills_rules_note(data))
    if skills_note:
        notes_to_write.append(('Skills and Rules', skills_note))

    concepts_note = safe_truncate(_build_concepts_note(data))
    if concepts_note:
        notes_to_write.append(('Concepts and Ideas', concepts_note))

    tools_note = safe_truncate(_build_tools_functions_note(data))
    if tools_note:
        notes_to_write.append(('Tools and Functions', tools_note))

    diagram_note = safe_truncate(_build_functional_diagram_note(data))
    if diagram_note:
        notes_to_write.append(('Functional Diagram', diagram_note))

    print(f'\nNotes to write: {[t for t,_ in notes_to_write]}')
    print(f'Target directory: {domain}')

    # Store notes
    async with get_client() as client:
        active_project = await get_active_project(client, project, None)
        knowledge_client = KnowledgeClient(client, active_project.external_id)

        for title, content in notes_to_write:
            if not content:
                continue
            entity = Entity(
                title=title,
                directory=domain,
                entity_type='note',
                content_type='text/markdown',
                content=content,
                entity_metadata={'tags': ['assimilated', domain]},
            )
            try:
                result = await knowledge_client.create_entity(entity.model_dump(), fast=False)
                print(f'  Stored: {title} -> {result.permalink}')
            except Exception as e:
                if '409' in str(e) or 'conflict' in str(e).lower() or 'already exists' in str(e).lower():
                    print(f'  Already exists: {title} (updating...)')
                    try:
                        if entity.permalink:
                            entity_id = await knowledge_client.resolve_entity(entity.permalink)
                            result = await knowledge_client.update_entity(entity_id, entity.model_dump(), fast=False)
                            print(f'  Updated: {title} -> {result.permalink}')
                    except Exception as e2:
                        print(f'  FAILED to update: {title}: {e2}')
                else:
                    print(f'  FAILED: {title}: {e}')

    print('\nDone!')

asyncio.run(run())
