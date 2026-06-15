"""
cache_service.py — Multi-level Cache Service (FASE 2)

Level 1: Semantic Cache  — Qdrant collection "query_cache" (cosine >= 0.95)
Level 2: Redis Cache     — Graph subgraph + prompt cache (TTL-based)
"""

import json
import logging
import time
import uuid
from typing import Optional

from .config import settings

logger = logging.getLogger(__name__)


# ===================================================================
# Level 1 — Semantic Cache (Qdrant)
# ===================================================================

class SemanticCache:
    """Cache using Qdrant vector similarity (cosine >= threshold)."""

    COLLECTION = "query_cache"
    VECTOR_DIM = 384  # paraphrase-multilingual-MiniLM-L12-v2 output dim

    def __init__(self, similarity_threshold: float = 0.95, ttl_hours: int = 24):
        self.threshold = similarity_threshold
        self.ttl_seconds = ttl_hours * 3600
        self._client = None
        self._hits = 0
        self._misses = 0

    def initialize(self):
        """Create Qdrant client and ensure collection exists."""
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams

        self._client = QdrantClient(url=settings.qdrant_url)

        collections = [c.name for c in self._client.get_collections().collections]
        if self.COLLECTION not in collections:
            self._client.create_collection(
                collection_name=self.COLLECTION,
                vectors_config=VectorParams(
                    size=self.VECTOR_DIM, distance=Distance.COSINE
                ),
            )
            logger.info("Created Qdrant collection: %s", self.COLLECTION)
        else:
            logger.info("Qdrant collection '%s' already exists.", self.COLLECTION)

    def get(self, query_embedding: list) -> Optional[str]:
        """Return cached response if a similar query exists."""
        if self._client is None:
            self._misses += 1
            return None
        try:
            results = self._client.search(
                collection_name=self.COLLECTION,
                query_vector=query_embedding,
                limit=1,
                score_threshold=self.threshold,
            )
            if results:
                payload = results[0].payload or {}
                cached_at = payload.get("cached_at", 0)
                if time.time() - cached_at < self.ttl_seconds:
                    self._hits += 1
                    logger.info(
                        "Semantic cache HIT (score=%.4f)", results[0].score
                    )
                    return payload.get("response")
            self._misses += 1
            return None
        except Exception as e:
            logger.warning("Semantic cache GET failed: %s", e)
            self._misses += 1
            return None

    def put(self, query_embedding: list, query_text: str, response: str):
        """Store a query→response pair."""
        if self._client is None:
            return
        try:
            self._client.upsert(
                collection_name=self.COLLECTION,
                points=[
                    {
                        "id": str(uuid.uuid4()),
                        "vector": query_embedding,
                        "payload": {
                            "query": query_text[:500],
                            "response": response,
                            "cached_at": time.time(),
                        },
                    }
                ],
            )
        except Exception as e:
            logger.warning("Semantic cache PUT failed: %s", e)

    def invalidate(self):
        """Drop and recreate the cache collection."""
        if self._client is None:
            return
        try:
            from qdrant_client.models import Distance, VectorParams

            self._client.delete_collection(self.COLLECTION)
            self._client.create_collection(
                collection_name=self.COLLECTION,
                vectors_config=VectorParams(
                    size=self.VECTOR_DIM, distance=Distance.COSINE
                ),
            )
            self._hits = 0
            self._misses = 0
            logger.info("Semantic cache invalidated.")
        except Exception as e:
            logger.warning("Semantic cache invalidation failed: %s", e)

    @property
    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 3) if total > 0 else 0,
        }


# ===================================================================
# Level 2 — Redis Cache (graph + prompt)
# ===================================================================

class RedisCache:
    """Key-value cache backed by Redis with TTL support."""

    def __init__(self):
        self._client = None
        self._hits = 0
        self._misses = 0

    def initialize(self):
        """Connect to Redis. Gracefully degrades if unavailable."""
        try:
            import redis

            self._client = redis.Redis.from_url(
                settings.redis_url, decode_responses=True
            )
            self._client.ping()
            logger.info("Redis connected: %s", settings.redis_url)
        except Exception as e:
            logger.warning(
                "Redis unavailable (%s). Graph/prompt cache disabled.", e
            )
            self._client = None

    def get(self, key: str) -> Optional[str]:
        if self._client is None:
            return None
        try:
            val = self._client.get(key)
            if val is not None:
                self._hits += 1
                return val
            self._misses += 1
            return None
        except Exception:
            return None

    def put(self, key: str, value: str, ttl_seconds: int = 900):
        if self._client is None:
            return
        try:
            self._client.setex(key, ttl_seconds, value)
        except Exception:
            pass

    def invalidate(self, pattern: str = "*"):
        if self._client is None:
            return
        try:
            keys = self._client.keys(pattern)
            if keys:
                self._client.delete(*keys)
            logger.info("Redis cache invalidated (pattern=%s).", pattern)
        except Exception:
            pass

    @property
    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 3) if total > 0 else 0,
        }


# ===================================================================
# Global singletons — initialized at FastAPI startup
# ===================================================================
semantic_cache = SemanticCache()
redis_cache = RedisCache()
