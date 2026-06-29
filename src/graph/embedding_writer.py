"""
embedding_writer.py — Batch Embedding Writer for Neo4j

Production-grade utility that writes vector embeddings to Neo4j nodes
using UNWIND batch queries instead of N sequential SET operations.

Old pattern (O(N) round-trips):
    for name, emb in zip(names, embeddings):
        client.run_query(f"MATCH (n:{label} {{name: $name}}) SET n.embedding = $emb", ...)

New pattern (O(N/batch_size) round-trips):
    writer.write_embeddings(label, names, embeddings)
"""

import logging
import time
from typing import List
from src.graph.client import GraphClient

logger = logging.getLogger(__name__)

# Default batch size — keeps query payload under ~8MB limit for Neo4j Bolt
_DEFAULT_BATCH_SIZE = 500


def _chunked(lst: list, n: int):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


class BatchEmbeddingWriter:
    """
    Writes embeddings to Neo4j nodes in batched UNWIND transactions.

    Usage
    -----
    writer = BatchEmbeddingWriter(client)
    writer.write_embeddings("SymptomPattern", names, embeddings)
    """

    def __init__(self, client: GraphClient, batch_size: int = _DEFAULT_BATCH_SIZE):
        self.client = client
        self.batch_size = batch_size

    def write_embeddings(
        self,
        label: str,
        names: List[str],
        embeddings: List[List[float]],
        match_property: str = "name",
    ) -> int:
        """
        Write embeddings to Neo4j nodes matching on `match_property`.

        Parameters
        ----------
        label : str
            Node label (e.g. "SymptomPattern")
        names : list[str]
            List of node property values to match on
        embeddings : list[list[float]]
            Corresponding embedding vectors
        match_property : str
            The node property used to match (default: "name")

        Returns
        -------
        int
            Total number of nodes updated
        """
        if len(names) != len(embeddings):
            raise ValueError(
                f"names ({len(names)}) and embeddings ({len(embeddings)}) must have the same length."
            )
        if not names:
            logger.info("write_embeddings: no data for label=%s, skipping.", label)
            return 0

        # Build UNWIND query — single query template regardless of label
        query = f"""
        UNWIND $batch AS row
        MATCH (n:{label} {{{match_property}: row.key}})
        SET n.embedding = row.emb
        """

        batch_data = [
            {"key": name, "emb": emb} for name, emb in zip(names, embeddings)
        ]

        total_batches = (len(batch_data) + self.batch_size - 1) // self.batch_size
        updated = 0
        start = time.time()

        for i, chunk in enumerate(_chunked(batch_data, self.batch_size), start=1):
            try:
                self.client.run_query(query, {"batch": chunk}, timeout=120.0)
                updated += len(chunk)
                logger.info(
                    "BatchEmbeddingWriter [%s] batch %d/%d — %d/%d nodes written (%.1fs)",
                    label,
                    i,
                    total_batches,
                    updated,
                    len(batch_data),
                    time.time() - start,
                )
            except Exception as e:
                logger.error(
                    "BatchEmbeddingWriter [%s] batch %d failed: %s", label, i, e
                )

        logger.info(
            "BatchEmbeddingWriter [%s] complete — %d nodes updated in %.2fs.",
            label,
            updated,
            time.time() - start,
        )
        return updated
