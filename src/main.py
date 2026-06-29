import hashlib
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

from src.agent.agent import Agent

class JSONFormatter(logging.Formatter):
    """Structured JSON log formatter."""
    def format(self, record):
        log_entry = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "extra_data"):
            log_entry.update(record.extra_data)
        return json.dumps(log_entry, ensure_ascii=False)

# Configure root logger with JSON format
_handler = logging.StreamHandler()
_handler.setFormatter(JSONFormatter())
logging.root.handlers = [_handler]
logging.root.setLevel(logging.INFO)

logger = logging.getLogger(__name__)


# ===== Lifespan: startup / shutdown (FASE 2) =====

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize long-running services at startup."""
    from src.services.embedding_service import embedding_svc
    from src.services.cache_service import semantic_cache, redis_cache
    from src.config import settings

    logger.info("=== STARTUP: Validating environment and security ===")
    if settings.env.lower() in ("staging", "production") and not settings.api_key:
        error_msg = f"CRITICAL: API Key must be set in {settings.env} environment."
        logger.critical(error_msg)
        raise RuntimeError(error_msg)

    logger.info("=== STARTUP: Embedding model uses lazy init — skipping preload ===")

    logger.info("=== STARTUP: Initializing caches ===")
    try:
        semantic_cache.initialize()
    except Exception as e:
        logger.warning("Semantic cache init failed (Qdrant may not be ready): %s", e)
    try:
        redis_cache.initialize()
    except Exception as e:
        logger.warning("Redis cache init failed: %s", e)

    logger.info("=== STARTUP COMPLETE ===")
    yield
    logger.info("=== SHUTDOWN ===")


app = FastAPI(title="EMR Fault Analyzer API", version="3.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Agent
agent = Agent()


# ===== Schemas =====

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    query: str
    chat_history: Optional[List[ChatMessage]] = []

class ChatResponse(BaseModel):
    answer: str
    chunks: Optional[List[str]] = []
    sql: Optional[str] = None
    sql_data: Optional[List[Dict[str, Any]]] = None
    graph_traversal: Optional[Dict[str, Any]] = None
    token_usage: Optional[Dict[str, int]] = {}
    cache_hit: Optional[str] = None
    timing_ms: Optional[Dict[str, float]] = None
    steps: Optional[List[Dict[str, Any]]] = []  # Reasoning trace

class CacheInvalidateRequest(BaseModel):
    level: str = "all"

@app.get("/health")
def health_check():
    """Check connectivity to all downstream services."""
    checks = {}
    overall = True

    # Neo4j
    try:
        from src.services.providers import get_graph_client
        gc = get_graph_client()
        with gc.driver.session() as s:
            s.run("RETURN 1").consume()
        checks["neo4j"] = "ok"
    except Exception as e:
        checks["neo4j"] = f"error: {e}"
        overall = False

    # Qdrant
    try:
        from src.services.providers import get_qdrant_client
        qc = get_qdrant_client()
        qc.get_collections()
        checks["qdrant"] = "ok"
    except Exception as e:
        checks["qdrant"] = f"error: {e}"
        overall = False

    # PostgreSQL (via Vanna)
    try:
        from src.services.providers import get_vanna
        vn = get_vanna()
        vn.run_sql("SELECT 1")
        checks["postgresql"] = "ok"
    except Exception as e:
        checks["postgresql"] = f"error: {e}"
        overall = False

    # Cloud LLM
    try:
        from src.services.providers import get_llm
        # Instantiate the LLM to verify configuration
        llm = get_llm(task_type="mini")
        checks["cloud_llm"] = "ok"
    except Exception as e:
        checks["cloud_llm"] = f"error: {e}"
        overall = False

    # Redis
    try:
        from src.services.cache_service import redis_cache
        if redis_cache._client and redis_cache._client.ping():
            checks["redis"] = "ok"
        else:
            checks["redis"] = "not connected"
    except Exception as e:
        checks["redis"] = f"error: {e}"

    # Embedding model
    try:
        from src.services.embedding_service import embedding_svc
        checks["embedding_model"] = "loaded" if embedding_svc._client is not None else "lazy (not yet used)"
    except Exception as e:
        checks["embedding_model"] = f"error: {e}"

    return {
        "status": "healthy" if overall else "degraded",
        "services": checks,
    }


from fastapi.security import APIKeyHeader
from fastapi import Security, Depends
from src.config import settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def verify_api_key(api_key: str = Depends(api_key_header)):
    is_prod_staging = settings.env.lower() in ("staging", "production")
    
    if is_prod_staging and not settings.api_key:
        raise HTTPException(
            status_code=500,
            detail="API Key is not configured in staging/production environment"
        )
        
    if is_prod_staging or settings.api_key:
        if not api_key or api_key != settings.api_key:
            raise HTTPException(status_code=403, detail="Invalid or missing API Key")

# ===================================================================
# /chat — with FASE 4 structured timing
# ===================================================================

@app.post("/chat", response_model=ChatResponse, dependencies=[Depends(verify_api_key)])
def chat(request: ChatRequest):
    request_id = str(uuid.uuid4())[:8]
    query_hash = hashlib.sha256(request.query.encode()).hexdigest()[:8]
    t_start = time.time()
    timings = {}

    try:
        from src.services.embedding_service import embedding_svc
        from src.services.cache_service import semantic_cache
        from src.agent.prompts import estimate_tokens

        # --- Step 1: Embedding ---
        t0 = time.time()
        query_embedding = embedding_svc.embed_query(request.query)
        timings["embedding_ms"] = round((time.time() - t0) * 1000, 1)

        # --- Step 2: Semantic cache check (DISABLED to prevent stale/incorrect results) ---
        # cached_response = semantic_cache.get(query_embedding)
        # timings["cache_check_ms"] = round((time.time() - t0) * 1000, 1)
        # if cached_response: ...
        timings["cache_check_ms"] = 0.0

        # --- Step 3: Agent pipeline ---
        chat_history = [msg.model_dump() for msg in request.chat_history] if request.chat_history else []

        t0 = time.time()
        response = agent.get_response(query=request.query, chat_history=chat_history)
        timings["agent_pipeline_ms"] = round((time.time() - t0) * 1000, 1)

        answer = response.get("answer", "")

        # Token counts
        prompt_tokens = estimate_tokens(request.query)
        completion_tokens = estimate_tokens(answer)

        # Store in cache (DISABLED to prevent stale data)
        # if answer and not answer.startswith("An error occurred"):
        #     semantic_cache.put(query_embedding, request.query, answer)
        pass

        timings["total_ms"] = round((time.time() - t_start) * 1000, 1)

        # FASE 4: Structured log with full timing breakdown
        from src.config import settings
        logger.info("Request completed", extra={"extra_data": {
            "request_id": request_id,
            "query_hash": query_hash,
            "cache_hit": "none",
            "step_timings": timings,
            "token_count": {"prompt": prompt_tokens, "completion": completion_tokens},
            "model_used": settings.model_provider,
        }})

        return ChatResponse(
            answer=answer,
            chunks=response.get("chunks", []),
            sql=response.get("sql"),
            sql_data=response.get("sql_data"),
            graph_traversal=response.get("graph_traversal"),
            token_usage={"prompt": prompt_tokens, "completion": completion_tokens},
            cache_hit=None,
            timing_ms=timings,
            steps=response.get("steps", []),
        )
    except Exception as e:
        timings["total_ms"] = round((time.time() - t_start) * 1000, 1)
        logger.error("Request failed", extra={"extra_data": {
            "request_id": request_id,
            "query_hash": query_hash,
            "error": str(e),
            "step_timings": timings,
        }})
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/chat/stream", dependencies=[Depends(verify_api_key)])
def chat_stream(request: ChatRequest):
    """Stream final LLM response with live status updates."""
    # Semantic cache disabled to guarantee live and accurate PostgreSQL/Qdrant queries
    pass

    return StreamingResponse(agent.stream_response(request.query), media_type="text/event-stream")


# ===== Cache management endpoints (FASE 2) =====

@app.post("/cache/invalidate", dependencies=[Depends(verify_api_key)])
def invalidate_cache(request: CacheInvalidateRequest):
    from src.services.cache_service import semantic_cache, redis_cache
    if request.level in ("all", "semantic"):
        semantic_cache.invalidate()
    if request.level in ("all", "graph"):
        redis_cache.invalidate("graph:*")
    return {"status": "ok", "invalidated": request.level}

@app.get("/cache/stats")
def cache_stats():
    from src.services.embedding_service import embedding_svc
    from src.services.cache_service import semantic_cache, redis_cache
    return {
        "embedding_cache": embedding_svc.cache_stats,
        "semantic_cache": semantic_cache.stats,
        "redis_cache": redis_cache.stats,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
