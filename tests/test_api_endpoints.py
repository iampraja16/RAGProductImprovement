import unittest
import sys
import os

# Adjust path to import from src
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from src.main import app
from src.config import settings

class TestApiEndpoints(unittest.TestCase):
    
    @classmethod
    def setUpClass(cls):
        # Configure app for test mode
        settings.env = "staging" # Enable API Key enforcement
        settings.api_key = "test_secret_key"
        cls.client = TestClient(app)

    def test_unauthenticated_access_chat(self):
        # /chat without header -> should be 403
        response = self.client.post("/chat", json={"query": "test query"})
        self.assertEqual(response.status_code, 403)
        self.assertIn("Invalid or missing API Key", response.json()["detail"])

    def test_unauthenticated_access_chat_stream(self):
        # /chat/stream without header -> should be 403
        response = self.client.post("/chat/stream", json={"query": "test query"})
        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_access_cache_invalidate(self):
        # /cache/invalidate without header -> should be 403
        response = self.client.post("/cache/invalidate", json={"level": "all"})
        self.assertEqual(response.status_code, 403)

    def test_authenticated_access_health(self):
        # /health should NOT require authentication
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)

    def test_authenticated_access_cache_stats(self):
        # /cache/stats should NOT require authentication
        response = self.client.get("/cache/stats")
        self.assertEqual(response.status_code, 200)

    def test_authenticated_access_chat_invalid_key(self):
        # /chat with incorrect header -> should be 403
        headers = {"X-API-Key": "wrong_key"}
        response = self.client.post("/chat", json={"query": "test query"}, headers=headers)
        self.assertEqual(response.status_code, 403)

    def test_authenticated_access_chat_valid_key(self):
        # /chat with correct header -> should run and return response
        from unittest.mock import patch
        headers = {"X-API-Key": "test_secret_key"}
        with patch("src.main.agent.get_response") as mock_get:
            mock_get.return_value = {
                "answer": "Mocked DB Answer",
                "chunks": [],
                "sql": "SELECT 1;",
                "sql_data": [{"val": 1}]
            }
            response = self.client.post("/chat", json={"query": "Test quantitative query?"}, headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["answer"], "Mocked DB Answer")
            
    def test_sandbox_sql_block_integration(self):
        # Test that ask_emr_database returns error/blocked message when unsafe query is passed
        from src.agent.tools import ask_emr_database
        from unittest.mock import patch
        
        # We mock get_vanna().generate_sql to return a malicious SQL query
        with patch("src.agent.tools.get_vanna") as mock_get_vanna:
            mock_vn = mock_get_vanna.return_value
            mock_vn.generate_sql.return_value = "DROP TABLE emr_records;"
            
            result = ask_emr_database("Delete all records")
            self.assertIn("Blocked unsafe or non-SELECT query", result["answer"])
            self.assertIsNone(result["sql_data"])

if __name__ == "__main__":
    unittest.main()
