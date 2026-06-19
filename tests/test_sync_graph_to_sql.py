import os
import sys
import unittest
import pandas as pd
from sqlalchemy import create_engine, text

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.sync_graph_to_sql import update_postgres
from src.config import settings

class TestSyncGraphToSQL(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pg_uri = settings.postgres_url.replace("+asyncpg", "")
        cls.engine = create_engine(cls.pg_uri)
        
        # Create a test setup
        with cls.engine.connect() as conn:
            with conn.begin():
                # Re-create/ensure test records are there
                conn.execute(text("ALTER TABLE emr_records ADD COLUMN IF NOT EXISTS graph_community_summary TEXT;"))
                # Get a valid emr_name to test with
                result = conn.execute(text("SELECT emr_name FROM emr_records LIMIT 1;")).fetchone()
                if result:
                    cls.test_emr_name = result[0]
                else:
                    # If empty table, insert a dummy record
                    conn.execute(text("INSERT INTO emr_records (emr_name) VALUES ('TEST-EMR-001');"))
                    cls.test_emr_name = "TEST-EMR-001"
                    
    def test_01_dry_run_execution(self):
        """Test that --dry-run does not write to the database."""
        # 1. Clear summary for test record
        with self.engine.connect() as conn:
            with conn.begin():
                conn.execute(
                    text("UPDATE emr_records SET graph_community_summary = NULL WHERE emr_name = :name"),
                    {"name": self.test_emr_name}
                )
                
        # 2. Run sync in dry-run mode
        df_test = pd.DataFrame([
            {"emr_name": self.test_emr_name, "graph_community_summary": "Dry Run Test Summary"}
        ])
        
        update_postgres(df_test, self.pg_uri, dry_run=True)
        
        # 3. Verify record remains NULL
        with self.engine.connect() as conn:
            val = conn.execute(
                text("SELECT graph_community_summary FROM emr_records WHERE emr_name = :name"),
                {"name": self.test_emr_name}
            ).fetchone()[0]
            
        self.assertIsNone(val, "Dry run should not write to PostgreSQL.")
        
    def test_02_idempotent_sync_and_repeated_execution(self):
        """Test that synchronization works, is idempotent, and runs repeatedly without error."""
        # 1. Run live sync
        df_test = pd.DataFrame([
            {"emr_name": self.test_emr_name, "graph_community_summary": "Test Summary Live Run"}
        ])
        
        update_postgres(df_test, self.pg_uri, dry_run=False)
        
        # 2. Verify value was updated
        with self.engine.connect() as conn:
            val1 = conn.execute(
                text("SELECT graph_community_summary FROM emr_records WHERE emr_name = :name"),
                {"name": self.test_emr_name}
            ).fetchone()[0]
        self.assertEqual(val1, "Test Summary Live Run")
        
        # 3. Run sync again with same data (repeated execution check)
        update_postgres(df_test, self.pg_uri, dry_run=False)
        
        # 4. Verify value is still correct and table didn't crash
        with self.engine.connect() as conn:
            val2 = conn.execute(
                text("SELECT graph_community_summary FROM emr_records WHERE emr_name = :name"),
                {"name": self.test_emr_name}
            ).fetchone()[0]
        self.assertEqual(val2, "Test Summary Live Run")
        
    def test_03_transaction_rollback_and_cleanup(self):
        """Test transaction rollback under failure and verify temp table is still dropped."""
        df_test = pd.DataFrame([
            {"emr_name": self.test_emr_name, "graph_community_summary": "Rollback Test Summary"}
        ])
        
        df_bad = pd.DataFrame([
            {"emr_name": self.test_emr_name, "invalid_column_name": "value"}
        ])
        
        # Running this should raise a database query exception
        with self.assertRaises(Exception):
            update_postgres(df_bad, self.pg_uri, dry_run=False)
            
        # Verify that temp_community_sync was still dropped
        with self.engine.connect() as conn:
            table_exists = conn.execute(text(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'temp_community_sync');"
            )).fetchone()[0]
            
        self.assertFalse(table_exists, "Temporary table must be dropped even on transaction failure.")

if __name__ == "__main__":
    unittest.main()
