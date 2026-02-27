#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run assimilation using the API client with correct URL."""
import asyncio
import sys
import io

# Set UTF-8 encoding for stdout and stderr
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
sys.path.insert(0, 'src')

async def run_assimilate_api_correct():
    try:
        from memopad.mcp.async_client import get_client
        from memopad.mcp.project_context import get_active_project
        from memopad.mcp.clients import KnowledgeClient
        from memopad.mcp.tools.assimilate import _is_github_repo, _clone_github_repo
        from memopad.mcp.tools.assimilate import _build_overview_note, _build_agent_profiles_note
        from memopad.mcp.tools.assimilate import _build_concepts_note, _build_tools_functions_note
        from memopad.mcp.tools.assimilate import _build_functional_diagram_note
        from memopad.schemas.base import Entity
        from urllib.parse import urlparse
        
        url = 'https://github.com/modelcontextprotocol/servers'
        project = 'main'
        
        print(f'Is GitHub repo: {_is_github_repo(url)}')
        print('Cloning repository...')
        data = await _clone_github_repo(url)
        print(f'Pages found: {len(data["pages"])}')
        
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace('www.', '')
        path_parts = parsed.path.strip('/').split('/')
        if len(path_parts) >= 2:
            domain = f"{domain}/{path_parts[0]}/{path_parts[1]}"
        
        print(f'Target directory: {domain}')
        
        async with get_client() as client:
            active_project = await get_active_project(client, project, None)
            knowledge_client = KnowledgeClient(client, active_project.external_id)
            
            notes_to_write = []
            
            overview = _build_overview_note(url, data)
            notes_to_write.append(('Overview', overview))
            
            agent_note = _build_agent_profiles_note(data)
            if agent_note:
                notes_to_write.append(('Agent Profiles', agent_note))
                
            concepts_note = _build_concepts_note(data)
            if concepts_note:
                notes_to_write.append(('Concepts and Ideas', concepts_note))
                
            tools_note = _build_tools_functions_note(data)
            if tools_note:
                notes_to_write.append(('Tools and Functions', tools_note))
                
            diagram_note = _build_functional_diagram_note(data)
            if diagram_note:
                notes_to_write.append(('Functional Diagram', diagram_note))
                
            for title, content in notes_to_write:
                entity = Entity(
                    title=title,
                    directory=domain,
                    entity_type='note',
                    content_type='text/markdown',
                    content=content,
                    entity_metadata={'tags': ['assimilated', domain]}
                )
                
                try:
                    result = await knowledge_client.create_entity(entity.model_dump(), fast=False)
                    print(f'Stored: {title} -> {result.permalink}')
                except Exception as e:
                    if '409' in str(e) or 'already exists' in str(e).lower():
                        print(f'Updating existing: {title}')
                        try:
                            entity_id = await knowledge_client.resolve_entity(entity.permalink or title)
                            result = await knowledge_client.update_entity(entity_id, entity.model_dump(), fast=False)
                            print(f'Updated: {title} -> {result.permalink}')
                        except Exception as update_err:
                            print(f'Error updating: {update_err}')
                    else:
                        print(f'Error: {type(e).__name__}: {e}')
                        
        print('Assimilation complete!')
        
    except Exception as e:
        print(f'Error: {type(e).__name__}: {e}')
        import traceback
        print(f'Traceback: {traceback.format_exc()}')

if __name__ == "__main__":
    asyncio.run(run_assimilate_api_correct())
