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
        with self.driver.session() as session:
            result = session.run(query, parameters)
            return [dict(record) for record in result]
