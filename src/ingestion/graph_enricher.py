"""
graph_enricher.py — Deterministic Graph Enrichment Module

Production-grade replacement for the inline deterministic enrichment code
in Notebook 2, Cell 6.

Old pattern (NB2 Cell 6 — per-row sequential Neo4j writes in threads):
    - 8 threads × 5 MERGE queries per row = lock contention + N round-trips

New pattern (batch-first):
    - Collect ALL enrichment data from the dataframe in Python first
    - Send batched UNWIND Cypher queries (one query per 500 rows)
    - Eliminates Neo4j write lock contention entirely
    - Reduces total Neo4j round-trips from O(N * 5) to O(N / 500 * 5)

Usage in Notebook 2 Cell 6:
-----------------------------
    from src.ingestion.graph_enricher import GraphEnricher
    enricher = GraphEnricher(client)
    enricher.enrich(df_sample)
"""

import logging
import re
import time
from typing import Any, Dict, List

import pandas as pd

from src.graph.client import GraphClient

logger = logging.getLogger(__name__)

_DEFAULT_BATCH_SIZE = 500
_ENRICH_TIMEOUT = 120.0

def _normalize_name(text: str) -> str:
    """Lowercase and collapse whitespace."""
    return re.sub(r"\s+", " ", str(text).strip()).lower()

def _safe(val: Any) -> str:
    """Return stripped string or empty string for NaN/None."""
    if val is None:
        return ""
    s = str(val).strip()
    return "" if s.lower() == "nan" else s

def _chunked(lst: list, n: int):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]

class GraphEnricher:
    """
    Deterministic graph enricher: adds Component, ActionPattern, and
    MachineModel nodes plus their relationships to Neo4j in batches.
    """

    def __init__(self, client: GraphClient, batch_size: int = _DEFAULT_BATCH_SIZE):
        self.client = client
        self.batch_size = batch_size

    def enrich(self, df: pd.DataFrame) -> None:
        """
        Run all enrichment steps for every row in *df*.

        Steps
        -----
        1. Component + HAS_COMPONENT + PART_OF (sub-component hierarchy)
        2. ActionPattern + MENTIONS + RESOLVED_BY (symptom/cause → action)
        3. MachineModel + ON_MACHINE relationship
        """
        start = time.time()
        total = len(df)
        logger.info("GraphEnricher: starting enrichment for %d rows …", total)

        comp_rows: List[Dict] = []
        sub_comp_rows: List[Dict] = []
        action_rows: List[Dict] = []
        machine_rows: List[Dict] = []
        site_rows: List[Dict] = []
        account_rows: List[Dict] = []
        smr_rows: List[Dict] = []

        for idx, row in df.iterrows():
            emr_name = _safe(row.get("EMR Name", row.get("emr_name", f"EMR-UNK-{idx}")))
            if not emr_name:
                continue

            comp = _safe(row.get("Techcare Component", ""))
            sub_comp = _safe(row.get("Techcare Sub Component", ""))
            action_raw = _safe(row.get("Action (How was problem corrected?)", ""))
            model = _safe(row.get("Machine Model", ""))
            product = _safe(row.get("Machine Product", row.get("machine_product", "")))
            site = _safe(row.get("Branch / Site", row.get("branch_site", "")))
            account = _safe(row.get("Account: Account Name", row.get("account_account_name", "")))
            smr = _safe(row.get("SMR Trouble", row.get("smr_trouble", "")))

            if comp:
                comp_rows.append({"emr": emr_name, "comp": comp})
            if comp and sub_comp:
                sub_comp_rows.append({"emr": emr_name, "comp": comp, "sub": sub_comp})
            if action_raw:
                action_rows.append({"emr": emr_name, "act": _normalize_name(action_raw)})
            if model:
                family = model.split("-")[0] if "-" in model else model
                machine_rows.append({
                    "emr": emr_name,
                    "name": model,
                    "family": family,
                    "product": product,
                })
            if site:
                site_rows.append({"emr": emr_name, "site": site})
            if account:
                account_rows.append({"emr": emr_name, "account": account})
            if smr:
                try:
                    smr_val = float(smr)
                    smr_rows.append({"emr": emr_name, "smr": smr_val})
                except ValueError:
                    pass

        logger.info(
            "GraphEnricher: collected comp=%d, sub_comp=%d, action=%d, machine=%d, site=%d, account=%d, smr=%d",
            len(comp_rows), len(sub_comp_rows), len(action_rows), len(machine_rows),
            len(site_rows), len(account_rows), len(smr_rows)
        )

        # ── Step 1: Components ─────────────────────────────────────────────
        self._batch_run(
            comp_rows,
            """
            UNWIND $batch AS row
            MERGE (c:Component {name: row.comp})
            WITH row, c
            MATCH (e:EMRRecord {emr_name: row.emr})
            MERGE (e)-[:HAS_COMPONENT]->(c)
            """,
            "Component+HAS_COMPONENT",
        )

        # ── Step 1b: Sub-components + PART_OF ─────────────────────────────
        self._batch_run(
            sub_comp_rows,
            """
            UNWIND $batch AS row
            MERGE (sub:Component {name: row.sub})
            MERGE (main:Component {name: row.comp})
            MERGE (sub)-[:PART_OF]->(main)
            WITH row, sub
            MATCH (e:EMRRecord {emr_name: row.emr})
            MERGE (e)-[:HAS_COMPONENT]->(sub)
            """,
            "SubComponent+PART_OF",
        )

        # ── Step 2: ActionPattern + RESOLVED_BY ───────────────────────────
        self._batch_run(
            action_rows,
            """
            UNWIND $batch AS row
            MERGE (a:ActionPattern {name: row.act})
            WITH row, a
            MATCH (e:EMRRecord {emr_name: row.emr})
            MERGE (e)-[:MENTIONS]->(a)
            WITH row, e, a
            OPTIONAL MATCH (e)-[:MENTIONS]->(s:SymptomPattern)
            FOREACH (s IN CASE WHEN s IS NOT NULL THEN [s] ELSE [] END |
                MERGE (s)-[:RESOLVED_BY]->(a)
            )
            WITH row, e, a
            OPTIONAL MATCH (e)-[:MENTIONS]->(rc:RootCausePattern)
            FOREACH (rc IN CASE WHEN rc IS NOT NULL THEN [rc] ELSE [] END |
                MERGE (rc)-[:RESOLVED_BY]->(a)
            )
            """,
            "ActionPattern+RESOLVED_BY",
        )

        # ── Step 3: MachineModel + ON_MACHINE ─────────────────────────────
        self._batch_run(
            machine_rows,
            """
            UNWIND $batch AS row
            MERGE (m:MachineModel {name: row.name, family: row.family})
            ON CREATE SET m.product = row.product
            ON MATCH SET m.product = CASE WHEN row.product <> '' THEN row.product ELSE m.product END
            WITH row, m
            MATCH (e:EMRRecord {emr_name: row.emr})
            MERGE (e)-[:ON_MACHINE]->(m)
            """,
            "MachineModel+ON_MACHINE",
        )

        # ── Step 4: Site + LOCATED_AT ─────────────────────────────────────
        self._batch_run(
            site_rows,
            """
            UNWIND $batch AS row
            MERGE (s:Site {name: row.site})
            WITH row, s
            MATCH (e:EMRRecord {emr_name: row.emr})
            MERGE (e)-[:LOCATED_AT]->(s)
            """,
            "Site+LOCATED_AT",
        )

        # ── Step 5: Account + BELONGS_TO ──────────────────────────────────
        self._batch_run(
            account_rows,
            """
            UNWIND $batch AS row
            MERGE (a:Account {name: row.account})
            WITH row, a
            MATCH (e:EMRRecord {emr_name: row.emr})
            MERGE (e)-[:BELONGS_TO]->(a)
            """,
            "Account+BELONGS_TO",
        )

        # ── Step 6: SMR Trouble Updates ───────────────────────────────────
        self._batch_run(
            smr_rows,
            """
            UNWIND $batch AS row
            MATCH (e:EMRRecord {emr_name: row.emr})
            SET e.smr_trouble = row.smr
            """,
            "EMRRecord+SMR",
        )

        logger.info(
            "GraphEnricher: all steps complete in %.1fs for %d rows.",
            time.time() - start,
            total,
        )

    def _batch_run(self, rows: List[Dict], query: str, step_name: str) -> None:
        """Send *rows* to Neo4j in batches using the given UNWIND query."""
        if not rows:
            logger.info("GraphEnricher [%s]: no data, skipping.", step_name)
            return

        total_batches = (len(rows) + self.batch_size - 1) // self.batch_size
        written = 0
        start = time.time()

        for i, chunk in enumerate(_chunked(rows, self.batch_size), start=1):
            try:
                self.client.run_query(query, {"batch": chunk}, timeout=_ENRICH_TIMEOUT)
                written += len(chunk)
                logger.info(
                    "GraphEnricher [%s] batch %d/%d — %d/%d rows (%.1fs)",
                    step_name, i, total_batches, written, len(rows),
                    time.time() - start,
                )
            except Exception as exc:
                logger.error(
                    "GraphEnricher [%s] batch %d failed: %s", step_name, i, exc
                )

        logger.info(
            "GraphEnricher [%s]: done — %d/%d rows written in %.2fs.",
            step_name, written, len(rows), time.time() - start,
        )
