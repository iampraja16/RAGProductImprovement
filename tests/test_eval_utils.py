import unittest
import sys
import os
import json
import time
from unittest.mock import MagicMock, patch

# Adjust path to import from workspace Cwd
sys.path.append(os.getcwd())

from eval.run_eval import save_atomic_json, save_atomic_text

class TestEvalUtils(unittest.TestCase):
    
    def setUp(self):
        self.test_json_path = "eval/test_checkpoint.json"
        self.test_text_path = "eval/test_text.txt"
        
    def tearDown(self):
        for path in [self.test_json_path, self.test_text_path, self.test_json_path + ".tmp", self.test_text_path + ".tmp"]:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass

    def test_atomic_json_write(self):
        data = {"status": "ok", "count": 42}
        save_atomic_json(self.test_json_path, data)
        
        # Verify file exists and was written correctly
        self.assertTrue(os.path.exists(self.test_json_path))
        with open(self.test_json_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        self.assertEqual(loaded, data)
        
    def test_atomic_text_write(self):
        text = "Hello Atomic World"
        save_atomic_text(self.test_text_path, text)
        
        # Verify file exists and was written correctly
        self.assertTrue(os.path.exists(self.test_text_path))
        with open(self.test_text_path, "r", encoding="utf-8") as f:
            loaded = f.read()
        self.assertEqual(loaded, text)

if __name__ == "__main__":
    unittest.main()
