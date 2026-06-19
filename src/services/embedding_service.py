"""
embedding_service.py — Persistent Embedding Service (FASE 2)

Long-running embedding model with:
- Model loaded ONCE at startup (not per-request)
- LRU cache for query embeddings (avoids re-encoding same text)
- Batch encoding support
- LangChain Embeddings interface compatible
"""

import hashlib
import logging
import time
from collections import OrderedDict
from typing import List, Optional

from langchain_core.embeddings import Embeddings
from sentence_transformers import SentenceTransformer

from src.config import settings

logger = logging.getLogger(__name__)


class EmbeddingService(Embeddings):
    """LangChain-compatible embedding service with LRU result cache."""

    def __init__(self, model_name: str = None, cache_size: int = 1024):
        self.model_name = model_name or settings.embedding_model
        self.model: Optional[SentenceTransformer] = None
        self._cache: OrderedDict = OrderedDict()
        self._cache_size = cache_size
        self._hits = 0
        self._misses = 0

    # ------ Lifecycle ------

    def load_model(self):
        """Load model into memory. Call once at startup."""
        logger.info("Loading embedding model: %s ...", self.model_name)
        start = time.time()
        self.model = SentenceTransformer(
            self.model_name, device="cpu"
        )
        elapsed = time.time() - start
        logger.info("Embedding model loaded in %.2fs", elapsed)

    def _ensure_model(self):
        if self.model is None:
            self.load_model()

    # ------ Cache helpers ------

    def _cache_key(self, text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    # ------ LangChain Embeddings interface ------

    def embed_query(self, text: str) -> List[float]:
        """Embed a single query with LRU cache."""
        key = self._cache_key(text)

        if key in self._cache:
            self._hits += 1
            self._cache.move_to_end(key)
            return self._cache[key]

        self._misses += 1
        self._ensure_model()

        embedding = self.model.encode(
            text, normalize_embeddings=True
        ).tolist()

        self._cache[key] = embedding
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

        return embedding

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Batch encode multiple texts (LangChain interface)."""
        return self.embed_batch(texts)

    # ------ Batch encoding ------

    def embed_batch(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        """Batch encode with per-item cache check."""
        self._ensure_model()

        results: List[Optional[List[float]]] = [None] * len(texts)
        uncached_indices = []
        uncached_texts = []

        for i, text in enumerate(texts):
            key = self._cache_key(text)
            if key in self._cache:
                self._hits += 1
                self._cache.move_to_end(key)
                results[i] = self._cache[key]
            else:
                self._misses += 1
                uncached_indices.append(i)
                uncached_texts.append(text)

        if uncached_texts:
            embeddings = self.model.encode(
                uncached_texts,
                batch_size=batch_size,
                normalize_embeddings=True,
            ).tolist()

            for idx, emb in zip(uncached_indices, embeddings):
                results[idx] = emb
                key = self._cache_key(texts[idx])
                self._cache[key] = emb
                if len(self._cache) > self._cache_size:
                    self._cache.popitem(last=False)

        return results

    # ------ Stats ------

    @property
    def cache_stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 3) if total > 0 else 0,
            "cache_entries": len(self._cache),
            "max_cache_size": self._cache_size,
        }


# Global singleton — initialized at FastAPI startup via lifespan
embedding_svc = EmbeddingService()
