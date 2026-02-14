#!/usr/bin/env python3
"""
Fixed Memopad MCP Server
This is a corrected implementation addressing common issues in MCP servers
"""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
import sys

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("memopad-server")

# MCP Protocol constants
PROTOCOL_VERSION = "2024-11-05"

class MemopadServer:
    """
    Fixed MemoServer implementation addressing common issues:
    1. Proper error handling and validation
    2. Thread-safe file operations
    3. Proper JSON-RPC 2.0 compliance
    4. Graceful shutdown handling
    5. Input sanitization
    6. Better file locking mechanisms
    """
    
    def __init__(self, storage_path: Optional[Path] = None):
        # FIX 1: Use proper default path with error handling
        if storage_path is None:
            storage_path = Path.home() / ".memopad" / "notes.json"
        
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        
        # FIX 2: Initialize storage file if it doesn't exist
        if not self.storage_path.exists():
            self._save_notes([])
        
        # FIX 3: Add lock for thread-safe operations
        self._lock = asyncio.Lock()
        
        logger.info(f"Memopad server initialized with storage at {self.storage_path}")
    
    def _load_notes(self) -> list[dict[str, Any]]:
        """
        FIX 4: Robust file loading with error recovery
        """
        try:
            with open(self.storage_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if not content:
                    return []
                return json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {e}. Creating backup and starting fresh.")
            # FIX 5: Backup corrupted file
            backup_path = self.storage_path.with_suffix('.json.bak')
            if self.storage_path.exists():
                self.storage_path.rename(backup_path)
            return []
        except FileNotFoundError:
            return []
        except Exception as e:
            logger.error(f"Unexpected error loading notes: {e}")
            return []
    
    def _save_notes(self, notes: list[dict[str, Any]]) -> None:
        """
        FIX 6: Atomic write operation to prevent corruption
        """
        temp_path = self.storage_path.with_suffix('.json.tmp')
        try:
            # Write to temporary file first
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(notes, f, indent=2, ensure_ascii=False)
            
            # FIX 7: Atomic rename (works on Unix and Windows)
            temp_path.replace(self.storage_path)
        except Exception as e:
            logger.error(f"Error saving notes: {e}")
            if temp_path.exists():
                temp_path.unlink()
            raise
    
    def _validate_note_input(self, title: str, content: str) -> tuple[bool, Optional[str]]:
        """
        FIX 8: Input validation to prevent issues
        """
        if not title or not isinstance(title, str):
            return False, "Title must be a non-empty string"
        
        if not content or not isinstance(content, str):
            return False, "Content must be a non-empty string"
        
        # FIX 9: Reasonable length limits
        if len(title) > 500:
            return False, "Title too long (max 500 characters)"
        
        if len(content) > 1_000_000:
            return False, "Content too long (max 1MB)"
        
        return True, None
    
    async def create_note(self, title: str, content: str) -> dict[str, Any]:
        """
        Create a new note with proper validation and error handling
        """
        # FIX 10: Validate input
        valid, error_msg = self._validate_note_input(title, content)
        if not valid:
            raise ValueError(error_msg)
        
        async with self._lock:
            notes = self._load_notes()
            
            # FIX 11: Generate proper unique ID
            note_id = max([note.get('id', 0) for note in notes], default=0) + 1
            
            new_note = {
                'id': note_id,
                'title': title.strip(),
                'content': content.strip(),
                'created_at': datetime.utcnow().isoformat() + 'Z',
                'updated_at': datetime.utcnow().isoformat() + 'Z'
            }
            
            notes.append(new_note)
            self._save_notes(notes)
            
            logger.info(f"Created note {note_id}: {title}")
            return new_note
    
    async def list_notes(self) -> list[dict[str, Any]]:
        """List all notes"""
        async with self._lock:
            return self._load_notes()
    
    async def get_note(self, note_id: int) -> Optional[dict[str, Any]]:
        """Get a specific note by ID"""
        async with self._lock:
            notes = self._load_notes()
            for note in notes:
                if note.get('id') == note_id:
                    return note
            return None
    
    async def update_note(self, note_id: int, title: Optional[str] = None, 
                          content: Optional[str] = None) -> Optional[dict[str, Any]]:
        """Update an existing note"""
        if title is not None or content is not None:
            if title is not None:
                valid, error_msg = self._validate_note_input(title, content or "temp")
                if not valid and "Title" in error_msg:
                    raise ValueError(error_msg)
        
        async with self._lock:
            notes = self._load_notes()
            for note in notes:
                if note.get('id') == note_id:
                    if title is not None:
                        note['title'] = title.strip()
                    if content is not None:
                        note['content'] = content.strip()
                    note['updated_at'] = datetime.utcnow().isoformat() + 'Z'
                    
                    self._save_notes(notes)
                    logger.info(f"Updated note {note_id}")
                    return note
            
            return None
    
    async def delete_note(self, note_id: int) -> bool:
        """Delete a note"""
        async with self._lock:
            notes = self._load_notes()
            original_len = len(notes)
            notes = [n for n in notes if n.get('id') != note_id]
            
            if len(notes) < original_len:
                self._save_notes(notes)
                logger.info(f"Deleted note {note_id}")
                return True
            
            return False
    
    async def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """
        FIX 12: Proper JSON-RPC 2.0 request handling
        """
        try:
            method = request.get('method')
            params = request.get('params', {})
            request_id = request.get('id')
            
            # FIX 13: Handle MCP protocol methods
            if method == 'initialize':
                return {
                    'jsonrpc': '2.0',
                    'id': request_id,
                    'result': {
                        'protocolVersion': PROTOCOL_VERSION,
                        'capabilities': {
                            'tools': {}
                        },
                        'serverInfo': {
                            'name': 'memopad-server',
                            'version': '1.0.0'
                        }
                    }
                }
            
            elif method == 'tools/list':
                return {
                    'jsonrpc': '2.0',
                    'id': request_id,
                    'result': {
                        'tools': [
                            {
                                'name': 'create_note',
                                'description': 'Create a new note',
                                'inputSchema': {
                                    'type': 'object',
                                    'properties': {
                                        'title': {
                                            'type': 'string',
                                            'description': 'Title of the note'
                                        },
                                        'content': {
                                            'type': 'string',
                                            'description': 'Text content of the note'
                                        }
                                    },
                                    'required': ['title', 'content']
                                }
                            },
                            {
                                'name': 'list_notes',
                                'description': 'List all notes',
                                'inputSchema': {
                                    'type': 'object',
                                    'properties': {}
                                }
                            },
                            {
                                'name': 'get_note',
                                'description': 'Get a specific note by ID',
                                'inputSchema': {
                                    'type': 'object',
                                    'properties': {
                                        'note_id': {
                                            'type': 'integer',
                                            'description': 'ID of the note to retrieve'
                                        }
                                    },
                                    'required': ['note_id']
                                }
                            },
                            {
                                'name': 'delete_note',
                                'description': 'Delete a note by ID',
                                'inputSchema': {
                                    'type': 'object',
                                    'properties': {
                                        'note_id': {
                                            'type': 'integer',
                                            'description': 'ID of the note to delete'
                                        }
                                    },
                                    'required': ['note_id']
                                }
                            }
                        ]
                    }
                }
            
            elif method == 'tools/call':
                tool_name = params.get('name')
                arguments = params.get('arguments', {})
                
                result = None
                if tool_name == 'create_note':
                    result = await self.create_note(
                        arguments.get('title'),
                        arguments.get('content')
                    )
                elif tool_name == 'list_notes':
                    result = await self.list_notes()
                elif tool_name == 'get_note':
                    result = await self.get_note(arguments.get('note_id'))
                elif tool_name == 'delete_note':
                    result = await self.delete_note(arguments.get('note_id'))
                else:
                    raise ValueError(f"Unknown tool: {tool_name}")
                
                return {
                    'jsonrpc': '2.0',
                    'id': request_id,
                    'result': {
                        'content': [
                            {
                                'type': 'text',
                                'text': json.dumps(result, indent=2)
                            }
                        ]
                    }
                }
            
            else:
                # FIX 14: Proper error for unknown methods
                return {
                    'jsonrpc': '2.0',
                    'id': request_id,
                    'error': {
                        'code': -32601,
                        'message': f'Method not found: {method}'
                    }
                }
        
        except Exception as e:
            logger.error(f"Error handling request: {e}", exc_info=True)
            return {
                'jsonrpc': '2.0',
                'id': request.get('id'),
                'error': {
                    'code': -32603,
                    'message': str(e)
                }
            }
    
    async def run(self):
        """
        FIX 15: Proper stdio transport handling
        """
        logger.info("Starting memopad server on stdio")
        
        try:
            while True:
                # Read JSON-RPC request from stdin
                line = await asyncio.get_event_loop().run_in_executor(
                    None, sys.stdin.readline
                )
                
                if not line:
                    break
                
                try:
                    request = json.loads(line)
                    response = await self.handle_request(request)
                    
                    # Write response to stdout
                    print(json.dumps(response), flush=True)
                    
                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON: {e}")
                    error_response = {
                        'jsonrpc': '2.0',
                        'id': None,
                        'error': {
                            'code': -32700,
                            'message': 'Parse error'
                        }
                    }
                    print(json.dumps(error_response), flush=True)
        
        except KeyboardInterrupt:
            logger.info("Server stopped by user")
        except Exception as e:
            logger.error(f"Server error: {e}", exc_info=True)
        finally:
            logger.info("Memopad server shutting down")


async def main():
    """Main entry point"""
    server = MemopadServer()
    await server.run()


if __name__ == '__main__':
    asyncio.run(main())
