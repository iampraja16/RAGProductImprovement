import os
import sys
import unittest
import tempfile
import json
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
from scripts.ingest_ppi import (
    _normalize_excel_columns,
    _deduplicate_emr,
    _build_ppi_summary_text,
    _safe_str,
    ingest_ppi,
)
from scripts.migrate_ppi import MIGRATE_SQL, MIGRATE_INDEXES
from scripts.sync_graph_to_sql import COLUMN_MAP
from src.agent.tools import _enrich_with_ppi


class TestMigratePpiIdempotency(unittest.TestCase):

    def test_migrate_sql_uses_if_not_exists(self):
        self.assertIn("IF NOT EXISTS", " ".join(MIGRATE_SQL))

    def test_migrate_indexes_use_if_not_exists(self):
        for idx in MIGRATE_INDEXES:
            self.assertIn("IF NOT EXISTS", idx)

    def test_migrate_has_all_ppi_columns(self):
        expected = {"ppi_external_id", "ppi_improvement_name", "ppi_phenomenon", "ppi_corrective_action"}
        found = set()
        for sql in MIGRATE_SQL:
            for col in expected:
                if col in sql:
                    found.add(col)
        missing = expected - found
        self.assertEqual(len(missing), 0, f"Missing columns in MIGRATE_SQL: {missing}")


class TestIngestPpiHelpers(unittest.TestCase):

    def setUp(self):
        self.sample_df = pd.DataFrame([
            {
                "EMR: EMR Name": "EMR-001",
                "PPI: External ID": "PPI.000004",
                "PPI: Improvement Name": "Techcare.PPI.000004",
                "PPI: Phenomenon": "Crack found on boom",
                "PPI: Corrective Action & Recommendation": "Replace boom section",
                "PPI: Symptom": "structural crack",
                "PPI: TechCare Component": "BOOM",
                "PPI: Machine Model": "PC200-10M0",
                "PPI: Subject": "boom crack inspection",
                "PPI: Created By": "tech_a",
                "PPI: Counter Measure by Principal": "Redesign bracket",
            }
        ])

    def test_normalize_excel_columns(self):
        result = _normalize_excel_columns(self.sample_df)
        expected_cols = {
            "emr_name", "ppi_external_id", "ppi_improvement_name",
            "ppi_phenomenon", "ppi_corrective_action", "ppi_symptom",
            "ppi_component", "ppi_model", "ppi_subject",
            "ppi_created_by", "ppi_counter_measure",
        }
        self.assertTrue(expected_cols.issubset(set(result.columns)))

    def test_deduplicate_emr(self):
        normalized = _normalize_excel_columns(self.sample_df)
        dup = pd.concat([normalized, normalized], ignore_index=True)
        result = _deduplicate_emr(dup)
        self.assertEqual(len(result), 1)

    def test_build_ppi_summary_text(self):
        row = self.sample_df.iloc[0].to_dict()
        normalized = _normalize_excel_columns(self.sample_df).iloc[0].to_dict()
        text = _build_ppi_summary_text(normalized)
        self.assertIn("PPI.000004", text)
        self.assertIn("Techcare.PPI.000004", text)
        self.assertIn("Crack found on boom", text)

    def test_safe_str(self):
        self.assertEqual(_safe_str(None), "")
        self.assertEqual(_safe_str("hello"), "hello")
        self.assertEqual(_safe_str("nan"), "")

    def test_dry_run_does_not_write(self):
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            self.sample_df.to_excel(f.name, index=False)
            temp_path = f.name
        try:
            result = ingest_ppi(excel_path=temp_path, dry_run=True)
            self.assertEqual(result["pg_updated"], 0)
            self.assertEqual(result["neo4j_merged"], 0)
        finally:
            os.unlink(temp_path)


class TestPpiEnrichmentHelper(unittest.TestCase):

    def test_enrich_with_ppi_no_emr_names(self):
        gc = MagicMock()
        gc.get_ppi_for_emrs.return_value = {}
        gc.find_ppi_by_symptom_component.return_value = []
        embedder = MagicMock()
        embedder.embed_query.return_value = [0.1] * 1536
        with patch("src.agent.tools.get_graph_client", return_value=gc), \
             patch("src.agent.tools.get_embeddings", return_value=embedder):
            result = _enrich_with_ppi([], query="engine overheat", max_fallback=3)
        self.assertEqual(result, "")

    def test_enrich_with_ppi_direct_match(self):
        gc = MagicMock()
        gc.get_ppi_for_emrs.return_value = {
            "EMR-001": [{"external_id": "PPI.000004", "improvement_name": "Techcare.PPI.000004", "phenomenon": "...", "corrective_action": "..."}]
        }
        with patch("src.agent.tools.get_graph_client", return_value=gc):
            result = _enrich_with_ppi(["EMR-001"])
        self.assertIn("PPI.000004", result)

    def test_enrich_with_ppi_fallback(self):
        gc = MagicMock()
        gc.get_ppi_for_emrs.return_value = {}
        gc.find_ppi_by_symptom_component.return_value = [
            {"external_id": "PPI.000017", "improvement_name": "Techcare.PPI.000017", "phenomenon": "...", "corrective_action": "...", "score": 0.89}
        ]
        embedder = MagicMock()
        embedder.embed_query.return_value = [0.1] * 1536
        with patch("src.agent.tools.get_graph_client", return_value=gc), \
             patch("src.agent.tools.get_embeddings", return_value=embedder):
            result = _enrich_with_ppi([], query="engine overheat", max_fallback=3)
        self.assertIn("PPI.000017", result)


class TestSyncGraphToSqlColumnMap(unittest.TestCase):

    def test_column_map_has_ppi_columns(self):
        ppi_cols = {"ppi_external_id", "ppi_improvement_name", "ppi_phenomenon", "ppi_corrective_action"}
        for col in ppi_cols:
            self.assertIn(col, COLUMN_MAP, f"COLUMN_MAP missing {col}")


class TestVannaTrainingPpiIntegration(unittest.TestCase):

    def test_schema_has_ppi_columns(self):
        schema_path = os.path.join(os.getcwd(), "vanna_training", "schema.sql")
        with open(schema_path, "r") as f:
            content = f.read()
        ppi_cols = {"ppi_external_id", "ppi_improvement_name", "ppi_phenomenon", "ppi_corrective_action"}
        for col in ppi_cols:
            self.assertIn(col, content, f"schema.sql missing {col}")

    def test_domain_docs_has_ppi_section(self):
        doc_path = os.path.join(os.getcwd(), "vanna_training", "domain_docs.md")
        with open(doc_path, "r") as f:
            content = f.read()
        self.assertIn("Product Problem Information (PPI)", content)

    def test_qa_pairs_has_ppi_examples(self):
        import yaml
        qa_path = os.path.join(os.getcwd(), "vanna_training", "qa_pairs.yaml")
        with open(qa_path, "r") as f:
            qa_data = yaml.safe_load(f)
        ppi_questions = [q for q in qa_data if "ppi" in q.get("question", "").lower() or "PPI" in q.get("sql", "")]
        self.assertGreaterEqual(len(ppi_questions), 15, f"Expected >=15 PPI examples, got {len(ppi_questions)}")


if __name__ == "__main__":
    unittest.main()
