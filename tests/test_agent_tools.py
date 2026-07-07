import unittest
import sys
import os
import pandas as pd

# Adjust path to import from workspace Cwd
sys.path.append(os.getcwd())

from src.agent.prompts import RAG_SYNTHESIZER_PROMPT
from src.agent.tools import ask_emr_database, _is_safe_select_query, _inject_limit_if_missing

class TestAgentTools(unittest.TestCase):
    
    # ----------------------------------------------------------------
    # 1. Provenance & Evidence Formatting Tests
    # ----------------------------------------------------------------
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

    # ----------------------------------------------------------------
    # 2. SQL Sandbox & Security Tests
    # ----------------------------------------------------------------
    def test_valid_select(self):
        queries = [
            "SELECT * FROM emr_records",
            "select emr_name, count(*) from emr_records group by emr_name",
            "  SELECT id FROM public.emr_records WHERE name = 'hydraulic leak'  ",
            "SELECT * FROM emr_records -- comment here",
            "SELECT * FROM emr_records /* multiline\ncomment */ WHERE id = 1",
        ]
        for q in queries:
            with self.subTest(query=q):
                self.assertTrue(_is_safe_select_query(q))

    def test_valid_with(self):
        queries = [
            "WITH symptom_count AS (SELECT name, count(*) as count FROM emr_records GROUP BY name) SELECT * FROM symptom_count WHERE count > 5",
            "  with temp as (select 1 as val) select * from temp",
        ]
        for q in queries:
            with self.subTest(query=q):
                self.assertTrue(_is_safe_select_query(q))

    def test_blocked_mutations(self):
        # Test basic mutations
        mutations = [
            "INSERT INTO emr_records (name) VALUES ('leak')",
            "UPDATE emr_records SET name = 'leak' WHERE id = 1",
            "DELETE FROM emr_records",
            "DROP TABLE emr_records",
            "ALTER TABLE emr_records ADD COLUMN new_col VARCHAR",
            "CREATE TABLE temp_table (id INT)",
            "TRUNCATE emr_records",
            "MERGE INTO emr_records USING other_table ON ...",
            "GRANT ALL PRIVILEGES ON emr_records TO hacker",
            "REVOKE SELECT ON emr_records FROM public",
            "COPY emr_records TO '/tmp/data.csv'",
            "CALL run_procedure()",
            "EXECUTE statement_name",
            "DO $$ BEGIN PERFORM 1; END $$",
            "VACUUM FULL emr_records",
            "ANALYZE emr_records",
        ]
        for q in mutations:
            with self.subTest(query=q):
                self.assertFalse(_is_safe_select_query(q))

    def test_multi_statement_attacks(self):
        attacks = [
            "SELECT 1; DROP TABLE emr_records;",
            "SELECT 1; INSERT INTO emr_records (name) VALUES ('hack');",
            "SELECT 1 \n;\n DELETE FROM emr_records;",
            "SELECT 1; SELECT 2;",
        ]
        for q in attacks:
            with self.subTest(query=q):
                self.assertFalse(_is_safe_select_query(q))

    def test_string_literal_edge_cases(self):
        # Keywords inside string literals should not be blocked
        queries = [
            "SELECT * FROM emr_records WHERE symptom = 'leak and delete'",
            "SELECT * FROM emr_records WHERE description = 'please insert new valve'",
            "SELECT * FROM emr_records WHERE action = 'update hydraulic cylinder'",
            "SELECT * FROM emr_records WHERE cause = 'drop in pressure'",
            "SELECT * FROM emr_records WHERE note = 'COPY of record'",
            "SELECT * FROM emr_records WHERE symptom = 'DO NOT USE'",
        ]
        for q in queries:
            with self.subTest(query=q):
                self.assertTrue(_is_safe_select_query(q))
                
    def test_inject_limit_if_missing(self):
        self.assertEqual(_inject_limit_if_missing("SELECT * FROM emr_records", 500), "SELECT * FROM emr_records LIMIT 500")
        self.assertEqual(_inject_limit_if_missing("SELECT * FROM emr_records;", 500), "SELECT * FROM emr_records LIMIT 500;")
        self.assertEqual(_inject_limit_if_missing("SELECT * FROM emr_records LIMIT 10", 500), "SELECT * FROM emr_records LIMIT 10")
        self.assertEqual(_inject_limit_if_missing("SELECT * FROM emr_records limit 20", 500), "SELECT * FROM emr_records limit 20")

if __name__ == "__main__":
    unittest.main()
