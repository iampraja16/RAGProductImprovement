"""Neo4j graph store client and index management."""

from neo4j import GraphDatabase
import logging

logger = logging.getLogger(__name__)

class GraphClient:
    def __init__(self, uri: str, user: str, password: str):
        self.uri = uri
        self.user = user
        self.password = password
        self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
        logger.info(f"Connected to Neo4j at {self.uri}")

    def close(self):
        self.driver.close()

    def run_query(self, query: str, parameters: dict = None) -> list[dict]:
        """Execute a raw Cypher query and return results as list of dicts."""
        parameters = parameters or {}
        from src.services.resilience import neo4j_breaker, resilient_call_with_fallback
        
        def _execute():
            # Apply a query timeout of 60s on session run for heavy GraphRAG operations
            with self.driver.session() as session:
                result = session.run(query, parameters, timeout=60.0)
                return [dict(record) for record in result]
                
        return resilient_call_with_fallback(neo4j_breaker, [], _execute)
