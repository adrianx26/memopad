#!/usr/bin/env python3
"""
Test Suite for Fixed Memopad MCP Server
Run with: python3 test_memopad.py
"""

import asyncio
import json
import tempfile
from pathlib import Path
import sys

# Import the server (adjust path as needed)
sys.path.insert(0, str(Path(__file__).parent))
from memopad_server_fixed import MemopadServer


class TestMemopadServer:
    """Comprehensive test suite for memopad server"""
    
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.tests_run = 0
    
    async def run_test(self, name: str, test_func):
        """Run a single test and track results"""
        self.tests_run += 1
        try:
            await test_func()
            print(f"✓ {name}")
            self.passed += 1
        except AssertionError as e:
            print(f"✗ {name}: {e}")
            self.failed += 1
        except Exception as e:
            print(f"✗ {name}: Unexpected error: {e}")
            self.failed += 1
    
    async def test_initialization(self):
        """Test server initialization"""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage_path = Path(tmpdir) / "notes.json"
            server = MemopadServer(storage_path)
            assert storage_path.exists(), "Storage file should be created"
            assert storage_path.parent.exists(), "Storage directory should exist"
    
    async def test_create_single_note(self):
        """Test creating a single note"""
        with tempfile.TemporaryDirectory() as tmpdir:
            server = MemopadServer(Path(tmpdir) / "notes.json")
            note = await server.create_note("Test Title", "Test Content")
            
            assert note['id'] == 1, "First note should have ID 1"
            assert note['title'] == "Test Title", "Title should match"
            assert note['content'] == "Test Content", "Content should match"
            assert 'created_at' in note, "Should have created_at timestamp"
            assert 'updated_at' in note, "Should have updated_at timestamp"
    
    async def test_create_multiple_notes(self):
        """Test creating multiple notes with unique IDs"""
        with tempfile.TemporaryDirectory() as tmpdir:
            server = MemopadServer(Path(tmpdir) / "notes.json")
            
            note1 = await server.create_note("Note 1", "Content 1")
            note2 = await server.create_note("Note 2", "Content 2")
            note3 = await server.create_note("Note 3", "Content 3")
            
            assert note1['id'] == 1, "First note ID should be 1"
            assert note2['id'] == 2, "Second note ID should be 2"
            assert note3['id'] == 3, "Third note ID should be 3"
    
    async def test_list_notes(self):
        """Test listing all notes"""
        with tempfile.TemporaryDirectory() as tmpdir:
            server = MemopadServer(Path(tmpdir) / "notes.json")
            
            await server.create_note("Note 1", "Content 1")
            await server.create_note("Note 2", "Content 2")
            
            notes = await server.list_notes()
            assert len(notes) == 2, "Should have 2 notes"
            assert notes[0]['title'] == "Note 1", "First note title should match"
            assert notes[1]['title'] == "Note 2", "Second note title should match"
    
    async def test_get_note(self):
        """Test retrieving a specific note"""
        with tempfile.TemporaryDirectory() as tmpdir:
            server = MemopadServer(Path(tmpdir) / "notes.json")
            
            created = await server.create_note("Test Note", "Test Content")
            retrieved = await server.get_note(created['id'])
            
            assert retrieved is not None, "Should retrieve the note"
            assert retrieved['id'] == created['id'], "IDs should match"
            assert retrieved['title'] == "Test Note", "Title should match"
    
    async def test_get_nonexistent_note(self):
        """Test retrieving a note that doesn't exist"""
        with tempfile.TemporaryDirectory() as tmpdir:
            server = MemopadServer(Path(tmpdir) / "notes.json")
            
            result = await server.get_note(999)
            assert result is None, "Should return None for nonexistent note"
    
    async def test_update_note(self):
        """Test updating a note"""
        with tempfile.TemporaryDirectory() as tmpdir:
            server = MemopadServer(Path(tmpdir) / "notes.json")
            
            note = await server.create_note("Original", "Original Content")
            updated = await server.update_note(note['id'], 
                                              title="Updated",
                                              content="Updated Content")
            
            assert updated is not None, "Should return updated note"
            assert updated['title'] == "Updated", "Title should be updated"
            assert updated['content'] == "Updated Content", "Content should be updated"
            assert updated['updated_at'] != note['updated_at'], "Timestamp should change"
    
    async def test_delete_note(self):
        """Test deleting a note"""
        with tempfile.TemporaryDirectory() as tmpdir:
            server = MemopadServer(Path(tmpdir) / "notes.json")
            
            note = await server.create_note("To Delete", "Content")
            result = await server.delete_note(note['id'])
            
            assert result is True, "Delete should return True"
            
            notes = await server.list_notes()
            assert len(notes) == 0, "Note should be deleted"
    
    async def test_delete_nonexistent_note(self):
        """Test deleting a note that doesn't exist"""
        with tempfile.TemporaryDirectory() as tmpdir:
            server = MemopadServer(Path(tmpdir) / "notes.json")
            
            result = await server.delete_note(999)
            assert result is False, "Should return False for nonexistent note"
    
    async def test_concurrent_creates(self):
        """Test concurrent note creation"""
        with tempfile.TemporaryDirectory() as tmpdir:
            server = MemopadServer(Path(tmpdir) / "notes.json")
            
            # Create 10 notes concurrently
            tasks = [
                server.create_note(f"Note {i}", f"Content {i}")
                for i in range(10)
            ]
            results = await asyncio.gather(*tasks)
            
            assert len(results) == 10, "Should create all notes"
            
            # Check all IDs are unique
            ids = [note['id'] for note in results]
            assert len(set(ids)) == 10, "All IDs should be unique"
            
            # Verify all notes were saved
            notes = await server.list_notes()
            assert len(notes) == 10, "All notes should be saved"
    
    async def test_input_validation_empty_title(self):
        """Test validation rejects empty title"""
        with tempfile.TemporaryDirectory() as tmpdir:
            server = MemopadServer(Path(tmpdir) / "notes.json")
            
            try:
                await server.create_note("", "Content")
                assert False, "Should raise ValueError"
            except ValueError as e:
                assert "Title" in str(e), "Error should mention title"
    
    async def test_input_validation_empty_content(self):
        """Test validation rejects empty content"""
        with tempfile.TemporaryDirectory() as tmpdir:
            server = MemopadServer(Path(tmpdir) / "notes.json")
            
            try:
                await server.create_note("Title", "")
                assert False, "Should raise ValueError"
            except ValueError as e:
                assert "Content" in str(e), "Error should mention content"
    
    async def test_input_validation_long_title(self):
        """Test validation rejects overly long title"""
        with tempfile.TemporaryDirectory() as tmpdir:
            server = MemopadServer(Path(tmpdir) / "notes.json")
            
            long_title = "x" * 501
            try:
                await server.create_note(long_title, "Content")
                assert False, "Should raise ValueError"
            except ValueError as e:
                assert "too long" in str(e).lower(), "Error should mention length"
    
    async def test_unicode_support(self):
        """Test support for Unicode characters"""
        with tempfile.TemporaryDirectory() as tmpdir:
            server = MemopadServer(Path(tmpdir) / "notes.json")
            
            # Test various Unicode characters
            note = await server.create_note(
                "测试 Test Тест 🎉",
                "Content with émojis 🚀 and spëcial çhars"
            )
            
            retrieved = await server.get_note(note['id'])
            assert retrieved['title'] == "测试 Test Тест 🎉", "Unicode title should be preserved"
            assert "🚀" in retrieved['content'], "Emojis should be preserved"
    
    async def test_persistence(self):
        """Test that notes persist across server instances"""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage_path = Path(tmpdir) / "notes.json"
            
            # Create notes with first server instance
            server1 = MemopadServer(storage_path)
            await server1.create_note("Persistent Note", "This should persist")
            
            # Create new server instance with same storage
            server2 = MemopadServer(storage_path)
            notes = await server2.list_notes()
            
            assert len(notes) == 1, "Note should persist"
            assert notes[0]['title'] == "Persistent Note", "Content should match"
    
    async def test_mcp_initialize(self):
        """Test MCP initialize method"""
        with tempfile.TemporaryDirectory() as tmpdir:
            server = MemopadServer(Path(tmpdir) / "notes.json")
            
            request = {
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'initialize',
                'params': {}
            }
            
            response = await server.handle_request(request)
            
            assert response['jsonrpc'] == '2.0', "Should have jsonrpc field"
            assert response['id'] == 1, "ID should match request"
            assert 'result' in response, "Should have result"
            assert 'protocolVersion' in response['result'], "Should have protocol version"
    
    async def test_mcp_tools_list(self):
        """Test MCP tools/list method"""
        with tempfile.TemporaryDirectory() as tmpdir:
            server = MemopadServer(Path(tmpdir) / "notes.json")
            
            request = {
                'jsonrpc': '2.0',
                'id': 2,
                'method': 'tools/list',
                'params': {}
            }
            
            response = await server.handle_request(request)
            
            assert 'result' in response, "Should have result"
            assert 'tools' in response['result'], "Should have tools list"
            
            tools = response['result']['tools']
            tool_names = [t['name'] for t in tools]
            
            assert 'create_note' in tool_names, "Should have create_note tool"
            assert 'list_notes' in tool_names, "Should have list_notes tool"
            assert 'get_note' in tool_names, "Should have get_note tool"
            assert 'delete_note' in tool_names, "Should have delete_note tool"
    
    async def test_mcp_tools_call_create(self):
        """Test MCP tools/call for create_note"""
        with tempfile.TemporaryDirectory() as tmpdir:
            server = MemopadServer(Path(tmpdir) / "notes.json")
            
            request = {
                'jsonrpc': '2.0',
                'id': 3,
                'method': 'tools/call',
                'params': {
                    'name': 'create_note',
                    'arguments': {
                        'title': 'MCP Test',
                        'content': 'Created via MCP'
                    }
                }
            }
            
            response = await server.handle_request(request)
            
            assert 'result' in response, "Should have result"
            assert 'content' in response['result'], "Should have content"
            
            # Parse the returned note
            note_text = response['result']['content'][0]['text']
            note = json.loads(note_text)
            
            assert note['title'] == 'MCP Test', "Title should match"
            assert note['content'] == 'Created via MCP', "Content should match"
    
    async def test_error_handling_invalid_method(self):
        """Test error handling for invalid method"""
        with tempfile.TemporaryDirectory() as tmpdir:
            server = MemopadServer(Path(tmpdir) / "notes.json")
            
            request = {
                'jsonrpc': '2.0',
                'id': 4,
                'method': 'invalid_method',
                'params': {}
            }
            
            response = await server.handle_request(request)
            
            assert 'error' in response, "Should have error"
            assert response['error']['code'] == -32601, "Should be method not found error"
    
    async def test_whitespace_trimming(self):
        """Test that whitespace is properly trimmed"""
        with tempfile.TemporaryDirectory() as tmpdir:
            server = MemopadServer(Path(tmpdir) / "notes.json")
            
            note = await server.create_note("  Title  ", "  Content  ")
            
            assert note['title'] == "Title", "Title should be trimmed"
            assert note['content'] == "Content", "Content should be trimmed"
    
    async def run_all_tests(self):
        """Run all tests and report results"""
        print("=" * 60)
        print("Running Memopad Server Test Suite")
        print("=" * 60)
        print()
        
        # Run all tests
        await self.run_test("Server Initialization", self.test_initialization)
        await self.run_test("Create Single Note", self.test_create_single_note)
        await self.run_test("Create Multiple Notes", self.test_create_multiple_notes)
        await self.run_test("List Notes", self.test_list_notes)
        await self.run_test("Get Note", self.test_get_note)
        await self.run_test("Get Nonexistent Note", self.test_get_nonexistent_note)
        await self.run_test("Update Note", self.test_update_note)
        await self.run_test("Delete Note", self.test_delete_note)
        await self.run_test("Delete Nonexistent Note", self.test_delete_nonexistent_note)
        await self.run_test("Concurrent Creates", self.test_concurrent_creates)
        await self.run_test("Validation: Empty Title", self.test_input_validation_empty_title)
        await self.run_test("Validation: Empty Content", self.test_input_validation_empty_content)
        await self.run_test("Validation: Long Title", self.test_input_validation_long_title)
        await self.run_test("Unicode Support", self.test_unicode_support)
        await self.run_test("Data Persistence", self.test_persistence)
        await self.run_test("MCP Initialize", self.test_mcp_initialize)
        await self.run_test("MCP Tools List", self.test_mcp_tools_list)
        await self.run_test("MCP Tools Call", self.test_mcp_tools_call_create)
        await self.run_test("Error Handling", self.test_error_handling_invalid_method)
        await self.run_test("Whitespace Trimming", self.test_whitespace_trimming)
        
        # Print summary
        print()
        print("=" * 60)
        print(f"Tests Run: {self.tests_run}")
        print(f"Passed: {self.passed} ✓")
        print(f"Failed: {self.failed} ✗")
        print(f"Success Rate: {(self.passed/self.tests_run*100):.1f}%")
        print("=" * 60)
        
        return self.failed == 0


async def main():
    """Main entry point"""
    test_suite = TestMemopadServer()
    success = await test_suite.run_all_tests()
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    asyncio.run(main())
