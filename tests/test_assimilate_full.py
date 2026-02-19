import asyncio
import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import sys
import os

# Ensure src is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

# Patch mcp.tool to return original function
with patch('memopad.mcp.server.mcp.tool') as mock_tool_decorator:
    mock_tool_decorator.side_effect = lambda *args, **kwargs: lambda func: func
    from memopad.mcp.tools.assimilate import assimilate

class TestAssimilateFiles(unittest.IsolatedAsyncioTestCase):
    
    @patch('memopad.mcp.tools.assimilate.webbrowser')
    @patch('memopad.mcp.tools.assimilate.httpx.AsyncClient')
    @patch('memopad.mcp.tools.assimilate.get_client')
    @patch('memopad.mcp.tools.assimilate.get_active_project')
    @patch('memopad.mcp.clients.KnowledgeClient') # Patching the class itself in its module
    async def test_open_browser(self, MockKnowledgeClient, mock_get_project, mock_get_client, mock_httpx, mock_browser):
        # Setup mocks
        mock_client_instance = AsyncMock()
        mock_httpx.return_value.__aenter__.return_value = mock_client_instance
        
        # Mock HEAD to return HTML
        mock_head_resp = MagicMock()
        mock_head_resp.headers = {"content-type": "text/html"}
        mock_client_instance.head.return_value = mock_head_resp
        
        # Mock GET for crawl
        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 200
        mock_get_resp.headers = {"content-type": "text/html"}
        mock_get_resp.text = "<html><body><h1>Test</h1></body></html>"
        mock_get_resp.url = "http://example.com"
        mock_client_instance.get.return_value = mock_get_resp

        # Mock DB
        mock_get_project.return_value = MagicMock(name="test_project", external_id="123")
        
        # Mock KnowledgeClient instance
        mock_k_client = AsyncMock()
        MockKnowledgeClient.return_value = mock_k_client
        mock_k_client.create_entity.return_value = MagicMock(permalink="http://memopad.local/123")
        
        # Call assimilate
        await assimilate("http://example.com", open_browser=True)
        
        # Verify browser opened
        mock_browser.open.assert_called_with("http://example.com")

    @patch('memopad.mcp.tools.assimilate.FileProcessor.extract_pdf_text')
    @patch('memopad.mcp.tools.assimilate.httpx.AsyncClient')
    @patch('memopad.mcp.tools.assimilate.get_client')
    @patch('memopad.mcp.tools.assimilate.get_active_project')
    @patch('memopad.mcp.clients.KnowledgeClient')
    async def test_direct_pdf_download(self, MockKnowledgeClient, mock_get_project, mock_get_client, mock_httpx, mock_extract_pdf):
        # Setup
        mock_client_instance = AsyncMock()
        mock_httpx.return_value.__aenter__.return_value = mock_client_instance
        
        # Mock GET response for PDF
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "application/pdf"}
        mock_resp.content = b"%PDF-1.4..."
        mock_resp.url = "http://example.com/doc.pdf"
        mock_client_instance.get.return_value = mock_resp
        
        mock_extract_pdf.return_value = "Extracted PDF content"
        
        mock_get_project.return_value = MagicMock(name="test_project", external_id="123")
        
        mock_k_client = AsyncMock()
        MockKnowledgeClient.return_value = mock_k_client
        mock_k_client.create_entity.return_value = MagicMock(permalink="http://memopad.local/doc")
        
        # Call assimilate with PDF URL
        await assimilate("http://example.com/doc.pdf")
        
        # Verify extraction happened
        mock_extract_pdf.assert_called_once()
        
        # Verify note storage (should be called at least once)
        mock_k_client.create_entity.assert_called()

if __name__ == '__main__':
    unittest.main()
