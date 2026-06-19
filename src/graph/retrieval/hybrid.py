"""Hybrid retrieval logic (Vector + Fulltext)."""

from typing import List, Dict
import logging

logger = logging.getLogger(__name__)

class HybridEntityRetriever:
    def __init__(self, graph_client):
        self.graph_client = graph_client

    def retrieve(self, query_text: str, query_vector: List[float], top_k: int, candidate_k: int = 20) -> List[Dict]:
        """Combine vector and keyword search results using naive ranker (max score)."""
        logger.info("Executing hybrid search (vector + fulltext)...")
        
        # 1. Vector Search
        vector_query = """
        CALL db.index.vector.queryNodes('symptom-embeddings', $k, $vector)
        YIELD node, score
        RETURN elementId(node) AS id, node.name AS name, labels(node)[0] as label, score
        """
        vector_rows = self.graph_client.run_query(vector_query, {"k": candidate_k, "vector": query_vector})
        
        # 2. Keyword Search (Fulltext)
        keyword_query = """
        CALL db.index.fulltext.queryNodes('entity-names', $query)
        YIELD node, score
        RETURN elementId(node) AS id, node.name AS name, labels(node)[0] as label, score
        """
        keyword_rows = self.graph_client.run_query(keyword_query, {"query": query_text})
        
        # 3. Merge and Score
        return self._merge_hybrid_scores(vector_rows, keyword_rows, top_k)

    def _merge_hybrid_scores(self, vector_rows: List[Dict], keyword_rows: List[Dict], top_k: int) -> List[Dict]:
        v_norm = self._normalized_scores(vector_rows)
        k_norm = self._normalized_scores(keyword_rows)
        
        all_ids = set(v_norm.keys()) | set(k_norm.keys())
        scores = {}
        
        # We need to map back to names and labels
        id_to_meta = {}
        for row in vector_rows + keyword_rows:
            if row["id"] not in id_to_meta:
                id_to_meta[row["id"]] = {"name": row["name"], "label": row["label"]}

        for entity_id in all_ids:
            v_score = v_norm.get(entity_id, 0.0)
            k_score = k_norm.get(entity_id, 0.0)
            # Naive ranker: take the max of normalized scores
            scores[entity_id] = max(v_score, k_score)
            
        ranked = sorted(scores.items(), key=lambda item: -item[1])
        
        result = []
        for entity_id, score in ranked[:top_k]:
            meta = id_to_meta[entity_id]
            result.append({
                "id": entity_id,
                "name": meta["name"],
                "label": meta["label"],
                "score": score
            })
            
        return result

    def _normalized_scores(self, rows: List[Dict]) -> Dict[str, float]:
        if not rows:
            return {}
        max_score = max(float(r["score"]) for r in rows)
        if max_score == 0:
            return {r["id"]: 0.0 for r in rows}
        return {r["id"]: float(r["score"]) / max_score for r in rows}
