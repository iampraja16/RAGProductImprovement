"""Service providers initialization — Cloud-native (Azure OpenAI / OpenAI API)."""

from functools import lru_cache
from typing import Optional, Dict
from urllib.parse import urlparse

from langchain_core.language_models import BaseChatModel
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient

from src.config import settings
from src.services.embedding_service import EmbeddingService, embedding_svc
from src.graph.client import GraphClient

from vanna.base import VannaBase
from vanna.qdrant import Qdrant_VectorStore as VannaQdrant_VectorStore


# ── Embeddings ────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_embeddings() -> EmbeddingService:
    return embedding_svc


# ── LLM Provider Factory ──────────────────────────────────────────────────────

@lru_cache(maxsize=4)
def get_llm(temperature: float = 0.0) -> BaseChatModel:
    """Return a cloud LLM client based on settings.llm_provider.

    Supports:
      "azure"  → AzureChatOpenAI  (requires AZURE_OPENAI_* env vars)
      "openai" → ChatOpenAI       (requires OPENAI_API_KEY env var)
    """
    provider = (settings.llm_provider or "azure").lower()

    if provider == "azure":
        from langchain_openai import AzureChatOpenAI
        return AzureChatOpenAI(
            azure_deployment=settings.azure_openai_deployment_name,
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
            temperature=temperature,
            timeout=30.0,
            max_retries=0,  # Retries managed by resilience.py
        )

    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            temperature=temperature,
            timeout=30.0,
            max_retries=0,  # Retries managed by resilience.py
        )

    raise ValueError(
        f"Unknown llm_provider='{provider}'. "
        "Set LLM_PROVIDER=azure or LLM_PROVIDER=openai in your .env file."
    )


# ── Graph Client ──────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_graph_client() -> GraphClient:
    return GraphClient(
        uri=settings.neo4j_uri,
        user=settings.neo4j_user,
        password=settings.neo4j_password,
    )


# ── Qdrant Client ─────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_qdrant_client(url: Optional[str] = None) -> QdrantClient:
    target = url or settings.qdrant_url
    parsed = urlparse(target)
    host = parsed.hostname or "localhost"
    return QdrantClient(host=host, port=6333, grpc_port=6334, prefer_grpc=True)


# ── Vanna SQL Analytics ───────────────────────────────────────────────────────

class _CloudVannaLLM:
    """Thin Vanna-compatible LLM bridge delegating to the shared get_llm() factory.

    Replaces the deleted OllamaVannaLLM class.  The cloud client (AzureChatOpenAI
    or ChatOpenAI) is invoked through the resilience-wrapped call site in submit_prompt.
    """

    def __init__(self, config=None):
        pass  # config unused; client is obtained from get_llm()

    def system_message(self, message: str) -> dict:
        return {"role": "system", "content": message}

    def user_message(self, message: str) -> dict:
        return {"role": "user", "content": message}

    def assistant_message(self, message: str) -> dict:
        return {"role": "assistant", "content": message}

    def submit_prompt(self, prompt: list[dict], **kwargs) -> str:
        from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
        from src.services.resilience import cloud_llm_breaker, resilient_call_with_fallback

        # Convert Vanna message dicts → LangChain message objects
        lc_messages = []
        for msg in prompt:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                lc_messages.append(SystemMessage(content=content))
            elif role == "assistant":
                lc_messages.append(AIMessage(content=content))
            else:
                lc_messages.append(HumanMessage(content=content))

        llm = get_llm(temperature=kwargs.get("temperature", 0.0))
        response = resilient_call_with_fallback(
            cloud_llm_breaker,
            "Error: LLM service unavailable (circuit breaker active or API timeout).",
            lambda: llm.invoke(lc_messages),
        )
        return response.content if hasattr(response, "content") else str(response)


class MyVanna(_CloudVannaLLM, VannaQdrant_VectorStore):
    def __init__(self, config: Optional[Dict] = None):
        VannaBase.__init__(self)
        VannaQdrant_VectorStore.__init__(self, config=config)
        _CloudVannaLLM.__init__(self, config=config)

    def log(self, message, title="Info"):
        pass

    def generate_embedding(self, data: str) -> list[float]:
        return get_embeddings().embed_query(data)

    def extract_sql(self, llm_response: str) -> str:
        # 1. Use Vanna's default extraction first (looks for ```sql)
        sql = super().extract_sql(llm_response)

        # 2. If it failed to clean (returned the raw string), do aggressive regex extraction
        if sql == llm_response:
            import re
            match = re.search(r"(?i)(SELECT\s+.+?;)", sql, re.DOTALL)
            if not match:
                match = re.search(r"(?i)(SELECT\s+.+)", sql, re.DOTALL)
            if match:
                sql = match.group(1)

        # 3. Final cleanup of any stray markdown, quotes, or trailing junk
        sql = sql.replace("```sql", "").replace("```", "").strip().strip('"').strip("'").strip()
        return sql


# ── Vanna Singleton Factory ───────────────────────────────────────────────────

def _pg_conn_kwargs_from_url(pg_url: str) -> Dict[str, Optional[str]]:
    parsed = urlparse(pg_url)
    return {
        "host": parsed.hostname,
        "dbname": parsed.path[1:] if parsed.path else None,
        "user": parsed.username,
        "password": parsed.password,
        "port": parsed.port,
    }


import threading
_cached_vanna: Optional[MyVanna] = None
_vanna_lock = threading.Lock()


def get_vanna(
    qdrant_url: Optional[str] = None,
    connect_postgres: bool = True,
    postgres_url: Optional[str] = None,
) -> MyVanna:
    global _cached_vanna
    with _vanna_lock:
        if _cached_vanna is not None:
            return _cached_vanna

        import logging
        logger = logging.getLogger(__name__)
        client = get_qdrant_client(url=qdrant_url)
        vn = MyVanna(config={"client": client})
        vn.allow_llm_to_see_data = False

        if connect_postgres:
            pg_url = postgres_url or settings.readonly_postgres_url
            conn_kwargs = _pg_conn_kwargs_from_url(pg_url)
            # Enforce statement timeout of 30 seconds
            conn_kwargs["options"] = "-c statement_timeout=30000"

            from src.services.resilience import postgres_breaker, resilient_call
            try:
                resilient_call(postgres_breaker, lambda: vn.connect_to_postgres(**conn_kwargs))
                _cached_vanna = vn
            except Exception as e:
                logger.error(f"PostgreSQL connection failed during initialization: {e}")
                return vn  # Return uncached instance on failure
        else:
            _cached_vanna = vn

        return vn
