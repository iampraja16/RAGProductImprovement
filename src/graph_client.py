"""
graph_client.py — Neo4j Knowledge Graph Client

Provides query functions for the EMR GraphRAG system:
- Symptom → Problem Cluster → Corrective Action traversal
- Cold start detection and fallback
- Hybrid retrieval (Phase 4)
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Dict, List, Optional

import numpy as np
from neo4j import GraphDatabase

from .config import settings

logger = logging.getLogger(__name__)


class GraphClient:
    """Client for querying the EMR knowledge graph in Neo4j."""

    def __init__(self, uri: str, user: str, password: str):
        # BEFORE: self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.driver = GraphDatabase.driver(
            uri, auth=(user, password),
            max_connection_pool_size=10,
            connection_timeout=5,
        )
        self._symptom_cache: Optional[List[Dict]] = None

    def close(self):
        self.driver.close()

    def _get_symptom_patterns_with_embeddings(self) -> List[Dict]:
        """Load all SymptomPattern nodes with their embeddings (cached)."""
        if self._symptom_cache is not None:
            return self._symptom_cache

        with self.driver.session() as session:
            result = session.run("""
                MATCH (sp:SymptomPattern)
                WHERE sp.embedding IS NOT NULL
                RETURN sp.name AS name, sp.embedding AS embedding
            """)
            self._symptom_cache = [
                {"name": r["name"], "embedding": np.array(r["embedding"])}
                for r in result
            ]
        logger.info("Loaded %d symptom patterns with embeddings.", len(self._symptom_cache))
        return self._symptom_cache

    def _find_nearest_symptom(self, query_embedding: np.ndarray) -> Dict[str, Any]:
        """Find the nearest SymptomPattern node via cosine similarity."""
        patterns = self._get_symptom_patterns_with_embeddings()

        if not patterns:
            return {"name": None, "similarity": 0.0}

        similarities = []
        for p in patterns:
            # Cosine similarity (embeddings are already normalized)
            sim = float(np.dot(query_embedding, p["embedding"]))
            similarities.append(sim)

        best_idx = int(np.argmax(similarities))
        return {
            "name": patterns[best_idx]["name"],
            "similarity": similarities[best_idx],
        }

    def find_solutions_for_symptom(self, query_embedding: np.ndarray, symptom_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Core GraphRAG query:
        1. Find nearest SymptomPattern node (cosine similarity)
        2. IF similarity >= threshold: traverse graph for solutions
        3. IF similarity < threshold: return cold_start=True (fallback)
        """
        threshold = settings.graph_similarity_threshold
        
        if symptom_name:
            best_match = {"name": symptom_name, "similarity": 1.0}
        else:
            best_match = self._find_nearest_symptom(query_embedding)
            
        if best_match["name"] is None or best_match["similarity"] < threshold:
            return {
                "cold_start": True,
                "best_guess": best_match.get("name", "Unknown"),
                "similarity": best_match.get("similarity", 0.0),
                "message": (
                    "Gejala ini belum pernah tercatat sebelumnya dalam knowledge graph. "
                    "Berikut hasil pencarian semantik terdekat sebagai referensi."
                ),
            }
 
        # Traverse graph: Symptom -> ProblemCluster -> RootCausePattern (Top 5) -> Actions
        with self.driver.session() as session:
            result = session.run("""
                MATCH (sp:SymptomPattern {name: $symptom_name})-[i:INDICATES]->(pc:ProblemCluster)
                WITH sp, pc, i
                MATCH (pc)-[hrc:HAS_ROOT_CAUSE]->(rc:RootCausePattern)
                WITH sp, pc, i, rc, hrc
                ORDER BY hrc.frequency DESC
                LIMIT 5
                
                // Cari ActionPattern yang terhubung ke RootCausePattern
                MATCH (rc)-[cr:RESOLVED_BY]->(ap:ActionPattern)
                
                // Validasi ketat: Pastikan ada tiket EMRRecord yang menghubungkan pc, rc, dan ap secara bersamaan
                MATCH (pc)<-[:BELONGS_TO]-(emr:EMRRecord)-[:RESOLVED_BY]->(ap)
                WHERE (emr)-[:CAUSED_BY]->(rc)
                
                OPTIONAL MATCH (ap)-[up:USES_PART]->(p:Part)
                RETURN sp.name AS symptom,
                       pc.label AS problem_cluster,
                       pc.cluster_id AS cluster_id,
                       i.frequency AS indicate_freq,
                       i.strength AS indicate_strength,
                       rc.name AS root_cause,
                       hrc.frequency AS cause_freq,
                       ap.name AS action,
                       cr.frequency AS action_freq,
                       COLLECT(DISTINCT {part_no: p.part_no, description: p.description, freq: up.frequency}) AS parts
                ORDER BY i.strength DESC, hrc.frequency DESC, cr.frequency DESC
            """, symptom_name=best_match["name"])
 
            records = list(result)
 
        if not records:
            return {
                "cold_start": True,
                "best_guess": best_match["name"],
                "similarity": best_match["similarity"],
                "message": (
                    f"Gejala cocok dengan '{best_match['name']}' "
                    f"(similarity: {best_match['similarity']:.2f}), "
                    "namun tidak ditemukan aksi perbaikan terkait di knowledge graph."
                ),
            }
 
        # Build structured result
        symptom = records[0]["symptom"]
        problem_cluster = records[0]["problem_cluster"]
        cluster_id = records[0]["cluster_id"]
 
        actions = []
        for r in records:
            parts = [
                p for p in r["parts"]
                if p.get("part_no") and p["part_no"] != "None"
            ]
            actions.append({
                "action": r["action"],
                "frequency": r["action_freq"],
                "root_cause": r["root_cause"],
                "cause_freq": r["cause_freq"],
                "parts": parts,
            })
 
        # Build traversal path for UI display
        traversal_path = {
            "symptom_matched": symptom,
            "similarity": best_match["similarity"],
            "problem_cluster": problem_cluster,
            "cluster_id": cluster_id,
            "indicate_freq": records[0]["indicate_freq"],
            "actions": actions,
        }
 
        return {
            "cold_start": False,
            "traversal_path": traversal_path,
        }
 
    def get_cluster_actions(self, cluster_id: int) -> List[Dict]:
        """Get all actions for a specific problem cluster, ranked by frequency."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (pc:ProblemCluster {cluster_id: $cid})
                      -[cr:COMMONLY_RESOLVED_BY]->(ap:ActionPattern)
                RETURN ap.name AS action, cr.frequency AS frequency
                ORDER BY cr.frequency DESC
                LIMIT 50
            """, cid=cluster_id)
            return [dict(r) for r in result]
 
    def get_action_details(self, action_name: str, model: Optional[str] = None) -> Dict:
        """Get parts and frequency for a specific action, optionally filtered by model."""
        with self.driver.session() as session:
            if model:
                result = session.run("""
                    MATCH (ap:ActionPattern {name: $action})
                          <-[:RESOLVED_BY]-(emr:EMRRecord)
                          -[:ON_MACHINE]->(mm:MachineModel {model: $model})
                    OPTIONAL MATCH (emr)-[:USED_PART]->(p:Part)
                    RETURN ap.name AS action,
                           mm.model AS model,
                           COUNT(DISTINCT emr) AS total_cases,
                           COLLECT(DISTINCT {part_no: p.part_no, desc: p.description}) AS parts
                    LIMIT 50
                """, action=action_name, model=model)
            else:
                result = session.run("""
                    MATCH (ap:ActionPattern {name: $action})
                          <-[:RESOLVED_BY]-(emr:EMRRecord)
                    OPTIONAL MATCH (emr)-[:USED_PART]->(p:Part)
                    RETURN ap.name AS action,
                           COUNT(DISTINCT emr) AS total_cases,
                           COLLECT(DISTINCT {part_no: p.part_no, desc: p.description}) AS parts
                    LIMIT 50
                """, action=action_name)
 
            record = result.single()
            if record:
                return dict(record)
            return {}
 
    def find_solutions_hybrid(self, query_embedding: np.ndarray, qdrant_chunks: List[str]) -> Dict[str, Any]:
        """
        Phase 4: Hybrid retrieval combining graph traversal + Qdrant results.
 
        1. Find top SymptomPattern matches from Neo4j
        2. For each match above threshold, traverse graph
        3. Merge with Qdrant results
        4. Return ranked recommendations with evidence from both sources
        """
        threshold = settings.graph_similarity_threshold
        patterns = self._get_symptom_patterns_with_embeddings()
 
        if not patterns:
            return {
                "cold_start": True,
                "graph_results": [],
                "vector_results": qdrant_chunks,
                "message": "Knowledge graph kosong. Menggunakan pencarian semantik saja.",
            }
 
        # Find top-3 matches
        similarities = [float(np.dot(query_embedding, p["embedding"])) for p in patterns]
        top_indices = np.argsort(similarities)[-3:][::-1]
        top_matches = [
            {"name": patterns[i]["name"], "similarity": similarities[i]}
            for i in top_indices
            if similarities[i] >= threshold
        ]
 
        if not top_matches:
            return {
                "cold_start": True,
                "best_guess": patterns[top_indices[0]]["name"] if patterns else None,
                "similarity": similarities[top_indices[0]] if patterns else 0.0,
                "graph_results": [],
                "vector_results": qdrant_chunks,
                "message": (
                    "Gejala belum tercatat di knowledge graph. "
                    "Menggabungkan hasil pencarian semantik."
                ),
            }
 
        # Traverse graph for each match
        graph_results = []
        for match in top_matches:
            result = self.find_solutions_for_symptom(query_embedding, symptom_name=match["name"])
            if not result.get("cold_start"):
                # Sinkronisasi nilai similarity riil dari kecocokan hybrid ini
                path = result["traversal_path"]
                path["similarity"] = match["similarity"]
                graph_results.append(path)
 
        return {
            "cold_start": False,
            "graph_results": graph_results,
            "vector_results": qdrant_chunks,
        }


def format_graph_result(result: Dict[str, Any]) -> str:
    """
    Format graph traversal into compact bullets for LLM context.
    Includes Root Cause Failure Analysis (RCFA) relationships.
    """
    if result.get("cold_start"):
        return result.get("message", "Tidak ada data di knowledge graph.")
 
    path = result.get("traversal_path", {})
    symptom = path.get("symptom_matched", "N/A")
    sim = path.get("similarity", 0)
    cluster = path.get("problem_cluster", "N/A")
    freq = path.get("indicate_freq", 0)
 
    lines = [
        f"- {symptom} --[INDICATES]--> {cluster} (score: {sim:.2f}, freq: {freq})",
    ]
 
    # Group actions by root cause to keep it sync with the graph UI
    rc_counts = {}
    for a in path.get("actions", []):
        rc = a.get("root_cause", "Penyebab Tidak Terdefinisi")
        if rc not in rc_counts:
            rc_counts[rc] = []
        rc_counts[rc].append(a)
 
    # Output up to 5 root causes, and up to 2 actions per root cause
    for rc, rc_actions in list(rc_counts.items())[:5]:
        rc_freq = rc_actions[0].get("cause_freq", 0)
        lines.append(f"  - {cluster} --[HAS_ROOT_CAUSE]--> {rc} ({rc_freq} cases)")
        
        for a in rc_actions[:2]:
            line = f"    - {rc} --[RESOLVED_BY]--> {a['action']} ({a['frequency']} cases)"
            parts = a.get("parts", [])
            valid = [p for p in parts if p.get("part_no") and p["part_no"] != "None"]
            if valid:
                pstr = ", ".join(f"{p.get('description','')}({p['part_no']})" for p in valid[:2])
                line += f"\n      Parts: {pstr}"
            lines.append(line)
 
    return "\n".join(lines)
