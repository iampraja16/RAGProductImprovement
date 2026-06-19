"""Community Detection using GDS Leiden Algorithm."""

import logging
from src.config import settings
from src.graph.client import GraphClient

logger = logging.getLogger(__name__)

class CommunityDetector:
    def __init__(self, client: GraphClient):
        self.client = client

    def detect(self, node_labels: list[str] = None, relationship_types: list[str] = None, max_levels: int = 3):
        logger.info("Starting Community Detection using GDS Leiden...")
        
        # Default to settings if not provided
        n_labels = node_labels if node_labels else ["SymptomPattern", "ProblemCluster", "RootCausePattern", "ActionPattern", "Part"]
        r_types = relationship_types if relationship_types else settings.community_relationship_types
        
        # Format for Cypher
        n_labels_str = str(n_labels) if n_labels != ["*"] else "'*'"
        
        # Convert relationship types list to a map with UNDIRECTED orientation
        rel_projection = {rel: {"orientation": "UNDIRECTED"} for rel in r_types}
        
        # 1. Project the graph into GDS in-memory catalog
        project_query = f"""
        CALL gds.graph.project(
            'entity-graph',
            {n_labels_str},
            $rel_projection
        )
        YIELD graphName, nodeCount, relationshipCount
        """
        
        try:
            # Drop if exists
            self.client.run_query("CALL gds.graph.drop('entity-graph', false)")
            
            result = self.client.run_query(project_query, {"rel_projection": rel_projection})
            logger.info(f"Projected graph: {result[0]['nodeCount']} nodes, {result[0]['relationshipCount']} relationships.")
        except Exception as e:
            logger.error(f"Failed to project graph to GDS. Is GDS plugin installed? Error: {e}")
            return False

        # 2. Run Leiden and write results back to Neo4j database
        # This will add a 'communityId' property to the nodes
        leiden_query = """
        CALL gds.leiden.write('entity-graph', {
            writeProperty: 'communityId',
            maxLevels: $max_levels,
            includeIntermediateCommunities: true,
            gamma: $gamma,
            theta: $theta
        })
        YIELD communityCount, modularities
        """
        
        try:
            result = self.client.run_query(leiden_query, {
                "max_levels": max_levels,
                "gamma": settings.community_gamma,
                "theta": settings.community_theta
            })
            logger.info(f"Leiden completed. Found {result[0]['communityCount']} communities.")
            logger.info(f"Modularities across levels: {result[0]['modularities']}")
            
            # 3. Create Community nodes and IN_COMMUNITY relationships
            self._build_community_hierarchy(max_levels)
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to run Leiden algorithm: {e}")
            return False
        finally:
            # Cleanup in-memory graph
            self.client.run_query("CALL gds.graph.drop('entity-graph', false)")

    def _build_community_hierarchy(self, max_levels: int):
        """Creates hierarchical (c:Community) nodes based on the communityId list property."""
        logger.info(f"Building Hierarchical Community nodes up to {max_levels} levels...")
        
        # 1. Level 0: Base nodes to Community Level 0
        query_l0 = """
        MATCH (n) WHERE n.communityId IS NOT NULL
        WITH n, CASE WHEN valueType(n.communityId) CONTAINS 'LIST' THEN n.communityId[0] ELSE n.communityId END AS c0_id
        MERGE (c0:Community {communityId: toString(c0_id), level: 0})
        MERGE (n)-[:IN_COMMUNITY]->(c0)
        """
        self.client.run_query(query_l0)
        
        # 2. Level N to Level N+1
        if max_levels > 1:
            for level in range(max_levels - 1):
                query_ln = f"""
                MATCH (n) WHERE n.communityId IS NOT NULL 
                AND valueType(n.communityId) CONTAINS 'LIST'
                AND size(n.communityId) > {level + 1}
                WITH DISTINCT n.communityId[{level}] AS child_id, n.communityId[{level + 1}] AS parent_id
                MERGE (child:Community {{communityId: toString(child_id), level: {level}}})
                MERGE (parent:Community {{communityId: toString(parent_id), level: {level + 1}}})
                MERGE (parent)-[:PARENT_OF]->(child)
                """
                self.client.run_query(query_ln)
                
        logger.info("Hierarchical Community hierarchy built.")
