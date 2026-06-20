import unittest
import sys
import os
import pandas as pd

# Adjust path to import from workspace Cwd
sys.path.append(os.getcwd())

from src.agent.prompts import RAG_SYNTHESIZER_PROMPT
from src.agent.tools import ask_emr_database
from src.graph.retrieval.drift import DriftSearchRetriever
from src.graph.retrieval.local import LocalSearchRetriever
from src.graph.retrieval.global_search import GlobalSearchRetriever

class TestProvenanceAndGrounding(unittest.TestCase):
    
    def test_synthesizer_prompt_contains_evidence_format(self):
        # 1. Verify prompt demands the exact divider
        self.assertIn("--- EVIDENCE/PROVENANCE ---", RAG_SYNTHESIZER_PROMPT)
        # 2. Verify prompt requests Neo4j nodes and SQL provenance info
        self.assertIn("Evidence Sources:", RAG_SYNTHESIZER_PROMPT)
        self.assertIn("Record Provenance:", RAG_SYNTHESIZER_PROMPT)

    def test_sql_tool_appends_metadata_provenance(self):
        # Mock Vanna instance so we don't need real DB connection for this test
        class DummyVanna:
            def generate_sql(self, q, **kwargs):
                return "SELECT emr_name, count(*) as fault_count FROM emr_records GROUP BY emr_name;"
            def run_sql(self, sql):
                return pd.DataFrame([
                    {"emr_name": "PC200-8 hydraulic failure", "fault_count": 5},
                    {"emr_name": "HD465 brake failure", "fault_count": 2}
                ])
                
        with unittest.mock.patch("src.agent.tools.get_vanna") as mock_get_vanna:
            mock_get_vanna.return_value = DummyVanna()
            
            result = ask_emr_database("Show me failures count")
            answer = result["answer"]
            
            # Assert answer context contains the explicit Metadata Provenance tag
            self.assertIn("Metadata Provenance:", answer)
            self.assertIn("Record Identifiers: PC200-8 hydraulic failure, HD465 brake failure", answer)
            self.assertIn("Aggregation Counts/Sums: 5, 2", answer)

    def test_ui_divider_split_logic(self):
        # Mock the UI separation logic
        content = "Ini adalah jawaban analisis.\n\n--- EVIDENCE/PROVENANCE ---\nEvidence Sources: Neo4j Node(s) [PC200], Community ID(s) [12]"
        divider = "--- EVIDENCE/PROVENANCE ---"
        
        self.assertIn(divider, content)
        parts = content.split(divider)
        narrative = parts[0].strip()
        evidence = parts[1].strip()
        
        self.assertEqual(narrative, "Ini adalah jawaban analisis.")
        self.assertEqual(evidence, "Evidence Sources: Neo4j Node(s) [PC200], Community ID(s) [12]")

if __name__ == "__main__":
    unittest.main()
