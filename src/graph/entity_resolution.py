"""Entity Resolution for merging similar nodes using vector embeddings."""

import logging
from typing import List, Dict, Any
from src.graph.client import GraphClient

logger = logging.getLogger(__name__)

class EntityResolver:
    def __init__(self, client: GraphClient):
        self.client = client

    def resolve_label(self, label: str, index_name: str, similarity_threshold: float = 0.95) -> int:
        """Finds highly similar nodes of a given label and merges them."""
        logger.info(f"Starting Entity Resolution for {label} (threshold: {similarity_threshold})")
        
        # We iterate over nodes and find similar ones.
        query_get_nodes = f"MATCH (n:{label}) WHERE n.embedding IS NOT NULL RETURN id(n) AS node_id, n.embedding AS embedding"
        nodes = self.client.run_query(query_get_nodes)
        
        merged_count = 0
        processed_ids = set()
        
        for node in nodes:
            node_id = node['node_id']
            if node_id in processed_ids:
                continue
                
            embedding = node['embedding']
            
            # Find similar nodes
            query_similar = f"""
            CALL db.index.vector.queryNodes('{index_name}', 10, $embedding)
            YIELD node, score
            WHERE score >= $threshold AND id(node) <> $node_id
            RETURN id(node) AS sim_id, score
            """
            similar_nodes = self.client.run_query(query_similar, {
                "embedding": embedding,
                "threshold": similarity_threshold,
                "node_id": node_id
            })
            
            if similar_nodes:
                sim_ids = [sn['sim_id'] for sn in similar_nodes if sn['sim_id'] not in processed_ids]
                if not sim_ids:
                    continue
                    
                # Merge secondary nodes into the primary node
                # properties: 'discard' means keep properties of the first node (primary)
                merge_query = """
                MATCH (primary) WHERE id(primary) = $primary_id
                MATCH (secondary) WHERE id(secondary) IN $secondary_ids
                WITH [primary] + collect(secondary) AS nodes
                CALL apoc.refactor.mergeNodes(nodes, {
                    properties: 'discard',
                    mergeRels: true
                })
                YIELD node
                RETURN id(node) AS merged_id
                """
                try:
                    self.client.run_query(merge_query, {
                        "primary_id": node_id,
                        "secondary_ids": sim_ids
                    })
                    merged_count += len(sim_ids)
                    processed_ids.update(sim_ids)
                    processed_ids.add(node_id)
                except Exception as e:
                    logger.warning(f"Failed to merge nodes {node_id} with {sim_ids}: {e}")
                    
        logger.info(f"Entity Resolution for {label} completed. Merged {merged_count} duplicate nodes.")
        return merged_count

    def resolve_all(self, similarity_threshold: float = 0.95) -> int:
        """Runs entity resolution for all configured patterns."""
        configs = [
            ("SymptomPattern", "symptom-embeddings"),
            ("ProblemCluster", "cluster-embeddings"),
            ("RootCausePattern", "rootcause-embeddings"),
            ("ActionPattern", "action-embeddings")
        ]
        
        total = 0
        for label, index_name in configs:
            total += self.resolve_label(label, index_name, similarity_threshold)
            
        logger.info(f"Total nodes merged across all labels: {total}")
        return total
