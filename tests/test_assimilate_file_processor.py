import unittest
import sys
import os

# Ensure src is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from memopad.mcp.tools.assimilate import FileProcessor

class TestFileProcessor(unittest.TestCase):
    def test_text_extraction(self):
        content = b"This is a test file."
        extracted = FileProcessor.extract_text_content(content, "text/plain", "http://example.com/test.txt")
        self.assertEqual(extracted, "This is a test file.")

    def test_csv_extraction(self):
        content = b"header1,header2\nval1,val2"
        extracted = FileProcessor.extract_text_content(content, "text/csv", "http://example.com/test.csv")
        self.assertEqual(extracted, "header1,header2\nval1,val2")
        
    def test_image_dispatch(self):
        # Invalid image content should return error message but prove dispatch happened
        content = b"not an image"
        extracted = FileProcessor.extract_text_content(content, "image/png", "http://example.com/test.png")
        self.assertTrue("Error processing image" in extracted)

if __name__ == '__main__':
    unittest.main()
