"""Neo4j graph store client and index management."""

from neo4j import GraphDatabase
import logging

logger = logging.getLogger(__name__)
_DEFAULT_QUERY_TIMEOUT = 60.0

# Salesforce base URL for PPI deep-links.
# Constructed at runtime from node property `record_id` (e.g. a1o2y000001L2WR).
# This matches the pattern used in notebook/7_ppi_ingestion.ipynb Step 5b
# and scripts/verify_ppi_links.py — no need to store salesforce_url in Neo4j.
SALESFORCE_BASE = "https://unitedtractors.my.salesforce.com"


def _normalize_uri(uri: str) -> str:
    """
    Replace 'localhost' with '127.0.0.1' in the Bolt URI.

    On Windows + WSL, Python resolves 'localhost' to IPv6 (::1) first.
    The WSL port relay (wslrelay.exe) only exposes IPv4, so the driver
    would fail on the IPv6 attempt before falling back to IPv4.
    Using '127.0.0.1' directly skips IPv6 resolution entirely.

    For other developers: this has no side effect — on Linux/macOS,
    '127.0.0.1' and 'localhost' are equivalent for Neo4j Bolt.
    Cloud URIs (bolt://neo4j.example.com) are unaffected.
    """
    return uri.replace("localhost", "127.0.0.1")


class GraphClient:
    def __init__(self, uri: str, user: str, password: str):
        self.uri = uri
        self.user = user
        self.password = password
        # Normalize localhost → 127.0.0.1 to force IPv4 on Windows/WSL
        _resolved_uri = _normalize_uri(uri)
        self.driver = GraphDatabase.driver(_resolved_uri, auth=(self.user, self.password))
        logger.info(f"Neo4j driver initialized: {uri} (resolved: {_resolved_uri})")

    def close(self):
        self.driver.close()

    def run_query(
        self,
        query: str,
        parameters: dict = None,
        timeout: float = _DEFAULT_QUERY_TIMEOUT,
    ) -> list[dict]:
        """
        Execute a raw Cypher query and return results as a list of dicts.

        Parameters
        ----------
        query : str
            Cypher query string.
        parameters : dict, optional
            Query parameters.
        timeout : float
            Per-query timeout in seconds (default 60 s).
            Use 120 s for batch UNWIND writes, 300 s for GDS / APOC operations.
        """
        parameters = parameters or {}
        from src.services.resilience import neo4j_breaker, resilient_call_with_fallback

        def _execute():
            with self.driver.session() as session:
                result = session.run(query, parameters, timeout=timeout)
                return [dict(record) for record in result]

        return resilient_call_with_fallback(neo4j_breaker, [], _execute)

    def get_ppi_for_emr(self, emr_name: str) -> list[dict]:
        query = """
        MATCH (e:EMRRecord {emr_name: $emr_name})-[:HAS_PPI]->(p:PPI)
        RETURN p.record_id AS record_id,
               p.external_id AS external_id,
               p.improvement_name AS improvement_name,
               p.phenomenon AS phenomenon,
               p.corrective_action AS corrective_action
        """
        rows = self.run_query(query, {"emr_name": emr_name})
        for row in rows:
            rid = row.get("record_id") or ""
            row["salesforce_url"] = f"{SALESFORCE_BASE}/{rid}" if rid else ""
        return rows

    def get_ppi_for_emrs(self, emr_names: list[str]) -> dict[str, list[dict]]:
        if not emr_names:
            return {}
        query = """
        MATCH (e:EMRRecord)-[:HAS_PPI]->(p:PPI)
        WHERE e.emr_name IN $emr_names
        RETURN e.emr_name AS emr_name,
               p.record_id AS record_id,
               p.external_id AS external_id,
               p.improvement_name AS improvement_name,
               p.phenomenon AS phenomenon,
               p.corrective_action AS corrective_action
        """
        rows = self.run_query(query, {"emr_names": emr_names})
        result: dict[str, list[dict]] = {}
        for row in rows:
            name = row["emr_name"]
            if name not in result:
                result[name] = []
            rid = row.get("record_id") or ""
            result[name].append({
                "record_id":         row["record_id"],
                "external_id":       row["external_id"],
                "improvement_name":  row["improvement_name"],
                "phenomenon":        row["phenomenon"],
                "corrective_action": row["corrective_action"],
                "salesforce_url":    f"{SALESFORCE_BASE}/{rid}" if rid else "",
            })
        return result

    def get_ppi_details_by_ids(self, ppi_ids: list[str]) -> list[dict]:
        if not ppi_ids:
            return []
        query = """
        MATCH (p:PPI)
        WHERE p.external_id IN $ppi_ids
        RETURN p.record_id AS record_id,
               p.external_id AS external_id,
               p.improvement_name AS improvement_name,
               p.phenomenon AS phenomenon,
               p.corrective_action AS corrective_action
        """
        rows = self.run_query(query, {"ppi_ids": ppi_ids})
        results = []
        for row in rows:
            rid = row.get("record_id") or ""
            row["salesforce_url"] = f"{SALESFORCE_BASE}/{rid}" if rid else ""
            results.append(row)
        return results

    def find_ppi_by_symptom_component(self, query_text: str, embedder, limit: int = 5, score_threshold: float = 0.0) -> list[dict]:
        vector = embedder.embed_query(query_text)
        query = """
        CALL db.index.vector.queryNodes('ppi-embeddings', $limit, $vector)
        YIELD node, score
        RETURN node.record_id AS record_id,
               node.external_id AS external_id,
               node.improvement_name AS improvement_name,
               node.phenomenon AS phenomenon,
               node.corrective_action AS corrective_action,
               score
        """
        results = self.run_query(query, {"limit": limit, "vector": vector})
        for row in results:
            rid = row.get("record_id") or ""
            row["salesforce_url"] = f"{SALESFORCE_BASE}/{rid}" if rid else ""
        if score_threshold > 0.0:
            results = [r for r in results if r.get("score", 0) >= score_threshold]
        return results
