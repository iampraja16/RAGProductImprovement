"""Neo4j graph store client and index management."""

from neo4j import GraphDatabase
import logging

logger = logging.getLogger(__name__)
_DEFAULT_QUERY_TIMEOUT = 60.0

class GraphClient:
    def __init__(self, uri: str, user: str, password: str):
        self.uri = uri
        self.user = user
        self.password = password
        self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
        logger.info(f"Connected to Neo4j at {self.uri}")

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
        RETURN p.external_id AS external_id,
               p.improvement_name AS improvement_name,
               p.phenomenon AS phenomenon,
               p.corrective_action AS corrective_action
        """
        return self.run_query(query, {"emr_name": emr_name})

    def get_ppi_for_emrs(self, emr_names: list[str]) -> dict[str, list[dict]]:
        if not emr_names:
            return {}
        query = """
        MATCH (e:EMRRecord)-[:HAS_PPI]->(p:PPI)
        WHERE e.emr_name IN $emr_names
        RETURN e.emr_name AS emr_name,
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
            result[name].append({
                "external_id": row["external_id"],
                "improvement_name": row["improvement_name"],
                "phenomenon": row["phenomenon"],
                "corrective_action": row["corrective_action"],
            })
        return result

    def find_ppi_by_symptom_component(self, query_text: str, embedder, limit: int = 5, score_threshold: float = 0.0) -> list[dict]:
        vector = embedder.embed_query(query_text)
        query = """
        CALL db.index.vector.queryNodes('ppi-embeddings', $limit, $vector)
        YIELD node, score
        RETURN node.external_id AS external_id,
               node.improvement_name AS improvement_name,
               node.phenomenon AS phenomenon,
               node.corrective_action AS corrective_action,
               score
        """
        results = self.run_query(query, {"limit": limit, "vector": vector})
        if score_threshold > 0.0:
            results = [r for r in results if r.get("score", 0) >= score_threshold]
        return results
