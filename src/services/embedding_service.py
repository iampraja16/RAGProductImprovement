"""
embedding_service.py — Cloud-Native Embedding Service

Replaces local SentenceTransformer with AzureOpenAIEmbeddings / OpenAIEmbeddings.

Retains:
- LangChain Embeddings interface (embed_query, embed_documents)
- LRU in-process cache (avoids re-encoding duplicate queries)
- Batch encoding support
- Single global singleton (embedding_svc) initialized at startup
"""

import hashlib
import logging
from collections import OrderedDict
from typing import List, Optional

from langchain_core.embeddings import Embeddings

from src.config import settings

logger = logging.getLogger(__name__)


class EmbeddingService(Embeddings):
    """LangChain-compatible embedding service backed by Azure OpenAI or OpenAI API.

    The embedding client is lazily initialized on first use to avoid blocking
    imports during test collection or worker startup.
    """

    def __init__(self, cache_size: int = 1024):
        self._client: Optional[Embeddings] = None
        self._cache: OrderedDict = OrderedDict()
        self._cache_size = cache_size
        self._hits = 0
        self._misses = 0

    # ── Client Initialization ─────────────────────────────────────────────────

    def _get_client(self) -> Embeddings:
        """Lazily initialize the cloud embedding client on first call."""
        if self._client is not None:
            return self._client

        provider = (settings.llm_provider or "azure").lower()

        if provider == "azure":
            from langchain_openai import AzureOpenAIEmbeddings
            self._client = AzureOpenAIEmbeddings(
                azure_deployment=settings.azure_openai_embedding_deployment,
                azure_endpoint=settings.azure_openai_endpoint,
                api_key=settings.azure_openai_api_key,
                api_version=settings.azure_openai_api_version,
            )
            logger.info(
                "EmbeddingService initialized: AzureOpenAIEmbeddings "
                "(deployment=%s)", settings.azure_openai_embedding_deployment
            )
        elif provider == "openai":
            from langchain_openai import OpenAIEmbeddings
            self._client = OpenAIEmbeddings(
                model=settings.openai_embedding_model,
                api_key=settings.openai_api_key,
            )
            logger.info(
                "EmbeddingService initialized: OpenAIEmbeddings (model=%s)",
                settings.openai_embedding_model,
            )
        else:
            raise ValueError(
                f"Unknown llm_provider='{provider}'. "
                "Set LLM_PROVIDER=azure or LLM_PROVIDER=openai in your .env file."
            )

        return self._client

    # ── Cache Helpers ─────────────────────────────────────────────────────────

    def _cache_key(self, text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    def _cache_get(self, key: str) -> Optional[List[float]]:
        if key in self._cache:
            self._hits += 1
            self._cache.move_to_end(key)
            return self._cache[key]
        self._misses += 1
        return None

    def _cache_put(self, key: str, embedding: List[float]) -> None:
        self._cache[key] = embedding
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    # ── LangChain Embeddings Interface ────────────────────────────────────────

    def embed_query(self, text: str) -> List[float]:
        """Embed a single query string with LRU cache."""
        key = self._cache_key(text)
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        embedding = self._get_client().embed_query(text)
        self._cache_put(key, embedding)
        return embedding

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Batch embed multiple texts, using cache for already-seen strings."""
        results: List[Optional[List[float]]] = [None] * len(texts)
        uncached_indices: List[int] = []
        uncached_texts: List[str] = []

        for i, text in enumerate(texts):
            key = self._cache_key(text)
            cached = self._cache_get(key)
            if cached is not None:
                results[i] = cached
            else:
                uncached_indices.append(i)
                uncached_texts.append(text)

        if uncached_texts:
            embeddings = self._get_client().embed_documents(uncached_texts)
            for idx, emb in zip(uncached_indices, embeddings):
                results[idx] = emb
                self._cache_put(self._cache_key(texts[idx]), emb)

        return results  # type: ignore[return-value]

    # ── Stats ─────────────────────────────────────────────────────────────────

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


# Global singleton — client is lazily initialized on first embed_query call
embedding_svc = EmbeddingService()
