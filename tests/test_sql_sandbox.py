import unittest
import sys
import os

# Adjust path to import from src
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agent.tools import _is_safe_select_query, _inject_limit_if_missing

class TestSqlSandbox(unittest.TestCase):
    
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
        # Limit missing
        self.assertEqual(_inject_limit_if_missing("SELECT * FROM emr_records", 500), "SELECT * FROM emr_records LIMIT 500")
        self.assertEqual(_inject_limit_if_missing("SELECT * FROM emr_records;", 500), "SELECT * FROM emr_records LIMIT 500;")
        # Limit already present
        self.assertEqual(_inject_limit_if_missing("SELECT * FROM emr_records LIMIT 10", 500), "SELECT * FROM emr_records LIMIT 10")
        self.assertEqual(_inject_limit_if_missing("SELECT * FROM emr_records LIMIT 10;", 500), "SELECT * FROM emr_records LIMIT 10;")
        # Case insensitive limit present
        self.assertEqual(_inject_limit_if_missing("SELECT * FROM emr_records limit 20", 500), "SELECT * FROM emr_records limit 20")
        # Semicolon spacing
        self.assertEqual(_inject_limit_if_missing("SELECT * FROM emr_records  ;  ", 500), "SELECT * FROM emr_records LIMIT 500;")

if __name__ == "__main__":
    unittest.main()
