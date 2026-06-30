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
