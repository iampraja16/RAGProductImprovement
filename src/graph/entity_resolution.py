"""Entity Resolution for merging similar nodes using vector embeddings.

Two-phase approach for production efficiency:
  Phase 1 — Parallel vector similarity discovery:
      ThreadPoolExecutor runs one vector-index query per node concurrently,
      instead of the previous sequential O(N) round-trip pattern.
      A threading.Lock protects the shared `processed_ids` / `merge_groups`
      state so no node is double-counted.

  Phase 2 — Batched APOC merge:
      All discovered (primary → secondaries) pairs are merged in Neo4j
      using APOC refactor, with a 300 s timeout to handle large groups.
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Set

from src.graph.client import GraphClient

logger = logging.getLogger(__name__)

_VECTOR_QUERY_TIMEOUT = 60.0
_APOC_MERGE_TIMEOUT = 300.0
_DEFAULT_QUERY_WORKERS = 8


class EntityResolver:
    def __init__(self, client: GraphClient):
        self.client = client

    def resolve_label(
        self,
        label: str,
        index_name: str,
        similarity_threshold: float = 0.95,
        query_workers: int = _DEFAULT_QUERY_WORKERS,
    ) -> int:
        """
        Find similar nodes for *label* and merge them.

        Phase 1 — parallel vector similarity queries (query_workers threads).
        Phase 2 — APOC mergeNodes for each discovered duplicate group.

        Returns the total number of secondary nodes merged (eliminated).
        """
        logger.info(
            "Entity Resolution [%s]: fetching nodes (threshold=%.2f) …",
            label,
            similarity_threshold,
        )

        nodes = self.client.run_query(
            f"MATCH (n:{label}) WHERE n.embedding IS NOT NULL "
            "RETURN elementId(n) AS node_id, n.embedding AS embedding"
        )

        if not nodes:
            logger.info("Entity Resolution [%s]: no embedded nodes found, skipping.", label)
            return 0

        logger.info("Entity Resolution [%s]: found %d nodes → running parallel similarity queries …", label, len(nodes))

        merge_groups: Dict[str, List[str]] = {} 
        processed_ids: Set[str] = set()
        lock = threading.Lock()

        query_similar = f"""
        CALL db.index.vector.queryNodes('{index_name}', 10, $embedding)
        YIELD node, score
        WHERE score >= $threshold AND elementId(node) <> $node_id
        RETURN elementId(node) AS sim_id, score
        """

        def _find_similar(node: dict) -> None:
            node_id = node["node_id"]
            with lock:
                if node_id in processed_ids:
                    return

            try:
                similar = self.client.run_query(
                    query_similar,
                    {
                        "embedding": node["embedding"],
                        "threshold": similarity_threshold,
                        "node_id": node_id,
                    },
                    timeout=_VECTOR_QUERY_TIMEOUT,
                )
            except Exception as exc:
                logger.warning("Vector query failed for node %s: %s", node_id, exc)
                return

            if not similar:
                return

            with lock:
                sim_ids = [
                    sn["sim_id"]
                    for sn in similar
                    if sn["sim_id"] not in processed_ids and sn["sim_id"] != node_id
                ]
                if not sim_ids:
                    return
                merge_groups[node_id] = sim_ids
                processed_ids.update(sim_ids)
                processed_ids.add(node_id)

        with ThreadPoolExecutor(max_workers=query_workers) as pool:
            futures = [pool.submit(_find_similar, node) for node in nodes]
            for f in as_completed(futures):
                exc = f.exception()
                if exc:
                    logger.warning("Similarity worker raised: %s", exc)

        logger.info(
            "Entity Resolution [%s]: Phase 1 complete — %d merge groups found.",
            label,
            len(merge_groups),
        )

        _merge_query = """
        MATCH (primary) WHERE elementId(primary) = $primary_id
        MATCH (secondary) WHERE elementId(secondary) IN $secondary_ids
        WITH primary, collect(secondary) AS secondaries
        WITH [primary] + secondaries AS nodes
        CALL apoc.refactor.mergeNodes(nodes, {properties: 'discard', mergeRels: true})
        YIELD node
        RETURN elementId(node) AS merged_id
        """

        merged_count = 0
        for primary_id, secondary_ids in merge_groups.items():
            try:
                self.client.run_query(
                    _merge_query,
                    {"primary_id": primary_id, "secondary_ids": secondary_ids},
                    timeout=_APOC_MERGE_TIMEOUT,
                )
                merged_count += len(secondary_ids)
            except Exception as exc:
                logger.warning(
                    "APOC merge failed for primary=%s → secondaries=%s: %s",
                    primary_id,
                    secondary_ids,
                    exc,
                )

        logger.info(
            "Entity Resolution [%s]: complete — %d duplicate nodes merged.",
            label,
            merged_count,
        )
        return merged_count

    def resolve_all(
        self,
        similarity_threshold: float = 0.95,
        max_workers: int = 4,
    ) -> int:
        """
        Run entity resolution for all node labels in parallel (one thread per label).

        max_workers controls label-level parallelism.
        Per-label vector query parallelism is controlled by _DEFAULT_QUERY_WORKERS.
        """
        configs = [
            ("SymptomPattern",   "symptom-embeddings"),
            ("ProblemCluster",   "cluster-embeddings"),
            ("RootCausePattern", "rootcause-embeddings"),
            ("ActionPattern",    "action-embeddings"),
        ]

        total = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    self.resolve_label, label, index_name, similarity_threshold
                ): label
                for label, index_name in configs
            }
            for future in as_completed(futures):
                label = futures[future]
                try:
                    count = future.result()
                    total += count
                    logger.info("resolve_all: label=%s merged %d nodes.", label, count)
                except Exception as exc:
                    logger.error("resolve_all: label=%s failed: %s", label, exc)

        logger.info("resolve_all: total nodes merged = %d", total)
        return total
