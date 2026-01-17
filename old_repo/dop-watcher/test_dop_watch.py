import unittest
from unittest.mock import patch, MagicMock
import json
import os
from pathlib import Path

# Import the main logic - we might need to adjust this if we want to import specific functions
# For this test, I'll mock the requests in the main execution path or refactor dop_watch.py to be testable.
# Easier approach: Write a test that imports main or functions.
# Since dop_watch.py runs main() on import if name==main, we should import it carefully or just patch requests before importing if possible?
# No, standard import is fine as long as we don't run main() immediately.
# But dop_watch.py has `if __name__ == "__main__": main()`, so importing is safe.

import dop_watch

class TestDopWatch(unittest.TestCase):
    def setUp(self):
        # Setup temporary files
        self.state_file = Path("dop-watcher/state_test.json")
        self.archive_file = Path("dop-watcher/dop-orders_test.md")
        
        # Point the module to these test files
        dop_watch.STATE_FILE = self.state_file
        dop_watch.ARCHIVE_FILE = self.archive_file
        
        # Clean up before test
        if self.state_file.exists(): self.state_file.unlink()
        if self.archive_file.exists(): self.archive_file.unlink()

    def tearDown(self):
        # Clean up after test
        if self.state_file.exists(): self.state_file.unlink()
        if self.archive_file.exists(): self.archive_file.unlink()

    @patch('dop_watch.requests.post')
    @patch('dop_watch.requests.get')
    def test_flow(self, mock_get, mock_post):
        # Mock Response
        mock_response = MagicMock()
        mock_response.text = """
        <html>
        <body>
            <a href="test_order_1.pdf">  Test Order 1  </a>
            <a href="http://example.com/other.pdf">External Order</a>
            <a href="ignore.txt">Ignore</a>
        </body>
        </html>
        """
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        # Run main
        dop_watch.main()

        # Check if state file was created
        self.assertTrue(self.state_file.exists())
        state = json.loads(self.state_file.read_text())
        self.assertIn("seen_urls", state)
        self.assertEqual(len(state["seen_urls"]), 2) # Should find both PDFs
        
        # Check Archive
        self.assertTrue(self.archive_file.exists())
        content = self.archive_file.read_text()
        self.assertIn("Test Order 1", content)
        self.assertIn("Initial Import", content) # First run

        # Run again - should find nothing new
        dop_watch.main()
        
        # Check calls
        # We expect requests.get to be called twice (once per main call)
        self.assertEqual(mock_get.call_count, 2)
        
        # notifications should have been sent only for the first run (2 items)
        self.assertEqual(mock_post.call_count, 2) 

if __name__ == '__main__':
    unittest.main()
