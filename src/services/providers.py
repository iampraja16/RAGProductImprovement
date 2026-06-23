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
def get_llm(temperature: float = 0.0, task_type: str = "reasoning") -> BaseChatModel:
    """Return a cloud LLM client based on settings.llm_provider and task_type.

    Supports:
      "azure"  → AzureChatOpenAI  (requires AZURE_OPENAI_* env vars)
      "openai" → ChatOpenAI       (requires OPENAI_API_KEY env var)
    """
    provider = (settings.model_provider or "azure").lower()

    if provider == "azure":
        from langchain_openai import AzureChatOpenAI
        deployment = settings.azure_openai_llm_deployment_name if task_type == "reasoning" else settings.azure_openai_mini_deployment_name
        return AzureChatOpenAI(
            azure_deployment=deployment,
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
            temperature=temperature,
            timeout=30.0,
            max_retries=0,  # Retries managed by resilience.py
        )

    if provider == "openai":
        from langchain_openai import ChatOpenAI
        model_name = settings.openai_model if task_type == "reasoning" else settings.openai_mini_model
        return ChatOpenAI(
            model=model_name,
            api_key=settings.openai_api_key,
            temperature=temperature,
            timeout=30.0,
            max_retries=0,  # Retries managed by resilience.py
        )

    raise ValueError(
        f"Unknown llm_provider='{provider}'. "
        "Set LLM_PROVIDER=azure or LLM_PROVIDER=openai in your .env file."
    )

@lru_cache(maxsize=4)
def get_failover_llm(temperature: float = 0.0, task_type: str = "reasoning") -> Optional[BaseChatModel]:
    """Return the secondary cloud LLM client if configured, else None."""
    if not settings.azure_openai_failover_endpoint:
        return None
        
    provider = (settings.model_provider or "azure").lower()
    if provider == "azure":
        from langchain_openai import AzureChatOpenAI
        deployment = settings.azure_openai_llm_deployment_name if task_type == "reasoning" else settings.azure_openai_mini_deployment_name
        return AzureChatOpenAI(
            azure_deployment=deployment,
            azure_endpoint=settings.azure_openai_failover_endpoint,
            api_key=settings.azure_openai_failover_api_key or settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
            temperature=temperature,
            timeout=30.0,
            max_retries=0,
        )
    return None

def invoke_with_failover(messages: list, task_type: str = "reasoning", temperature: float = 0.0, tools: list = None, **kwargs):
    """Invoke LLM with automatic failover to secondary endpoint, honoring circuit breakers."""
    from src.services.resilience import cloud_llm_breaker, failover_llm_breaker, resilient_call, _is_rate_limit_error
    from src.services.telemetry import tracer
    import logging
    import time
    logger = logging.getLogger(__name__)

    with tracer.start_as_current_span("invoke_with_failover") as span:
        start_time = time.time()
        span.set_attribute("task_type", task_type)
        span.set_attribute("model_name", settings.azure_openai_llm_deployment_name if task_type == "reasoning" else settings.azure_openai_mini_deployment_name)
        
        primary = get_llm(temperature=temperature, task_type=task_type)
        if tools:
            primary = primary.bind_tools(tools)
            
        secondary = get_failover_llm(temperature=temperature, task_type=task_type)
        if tools and secondary:
            secondary = secondary.bind_tools(tools)
            
        if cloud_llm_breaker.state == "OPEN" and secondary:
            logger.info(f"Failover active: Routing invoke ({task_type}) to secondary LLM.")
            span.set_attribute("failover_active", True)
            res = resilient_call(failover_llm_breaker, secondary.invoke, messages, **kwargs)
            span.set_attribute("response_time_ms", (time.time() - start_time) * 1000)
            return res
            
        span.set_attribute("failover_active", False)
        try:
            res = resilient_call(cloud_llm_breaker, primary.invoke, messages, **kwargs)
            span.set_attribute("response_time_ms", (time.time() - start_time) * 1000)
            return res
        except Exception as e:
            if _is_rate_limit_error(e):
                span.record_exception(e)
                raise e # Do not failover on HTTP 429 Rate Limit
                
            if secondary:
                logger.warning(f"Primary LLM failed: {e}. Activating failover to secondary LLM.")
                span.set_attribute("failover_active", True)
                res = resilient_call(failover_llm_breaker, secondary.invoke, messages, **kwargs)
                span.set_attribute("response_time_ms", (time.time() - start_time) * 1000)
                return res
            span.record_exception(e)
            raise e



def stream_with_failover(messages: list, task_type: str = "reasoning", temperature: float = 0.0, **kwargs):
    """Return LLM stream generator and active breaker, honoring failover states."""
    from src.services.resilience import cloud_llm_breaker, failover_llm_breaker, CircuitBreakerOpenException
    from src.services.telemetry import tracer
    import logging
    logger = logging.getLogger(__name__)

    with tracer.start_as_current_span("stream_with_failover") as span:
        span.set_attribute("task_type", task_type)
        span.set_attribute("model_name", settings.azure_openai_llm_deployment_name if task_type == "reasoning" else settings.azure_openai_mini_deployment_name)

        primary = get_llm(temperature=temperature, task_type=task_type)
        secondary = get_failover_llm(temperature=temperature, task_type=task_type)
        
        if cloud_llm_breaker.state == "OPEN" and secondary:
            logger.info(f"Failover active: Routing stream ({task_type}) to secondary LLM.")
            span.set_attribute("failover_active", True)
            failover_llm_breaker.check_state()
            return secondary.stream(messages, **kwargs), failover_llm_breaker
            
        span.set_attribute("failover_active", False)
        try:
            cloud_llm_breaker.check_state()
            return primary.stream(messages, **kwargs), cloud_llm_breaker
        except CircuitBreakerOpenException:
            if secondary:
                logger.warning("Primary LLM circuit breaker OPEN. Activating failover to secondary LLM.")
                span.set_attribute("failover_active", True)
                failover_llm_breaker.check_state()
                return secondary.stream(messages, **kwargs), failover_llm_breaker
            raise




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

        try:
            response = invoke_with_failover(lc_messages, temperature=kwargs.get("temperature", 0.0))
            return response.content if hasattr(response, "content") else str(response)
        except Exception as e:
            from src.services.resilience import _is_rate_limit_error
            if _is_rate_limit_error(e):
                return "Error: Layanan LLM sedang sibuk (Rate Limit). Silakan coba lagi."
            return "Error: LLM service unavailable (circuit breakers active or API timeout)."


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
