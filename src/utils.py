from __future__ import annotations

from functools import lru_cache
from typing import Optional, Dict
from urllib.parse import urlparse
import requests

# BEFORE: from langchain_huggingface import HuggingFaceEmbeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from langchain_ollama import ChatOllama
from .embedding_service import EmbeddingService, embedding_svc

from vanna.base import VannaBase
from vanna.qdrant import Qdrant_VectorStore as VannaQdrant_VectorStore

from .config import settings
from .graph_client import GraphClient

# BEFORE: @lru_cache → HuggingFaceEmbeddings (loaded lazily, no result cache)
def get_embeddings() -> EmbeddingService:
    """Return the global EmbeddingService singleton (loaded at startup, LRU cached)."""
    return embedding_svc

@lru_cache(maxsize=4)
def get_llm(temperature: float = 0.0) -> ChatOllama:
    """Return a local ChatOllama model."""
    return ChatOllama(
        model=settings.ollama_model,
        base_url=settings.ollama_base_url,
        temperature=temperature,
        num_ctx=4096,
    )

@lru_cache(maxsize=1)
def get_qdrant_client(url: Optional[str] = None) -> QdrantClient:
    """Return a Qdrant client with gRPC preferred."""
    # BEFORE: QdrantClient(url=url or settings.qdrant_url)  # REST only
    target = url or settings.qdrant_url
    parsed = urlparse(target)
    host = parsed.hostname or "localhost"
    return QdrantClient(host=host, port=6333, grpc_port=6334, prefer_grpc=True)

@lru_cache(maxsize=1)
def get_vector_store(
    collection_name: str = None,
    embeddings: Optional[HuggingFaceEmbeddings] = None,
    qdrant_url: Optional[str] = None,
) -> QdrantVectorStore:
    """Return a Qdrant VectorStore for an existing collection."""
    client = get_qdrant_client(url=qdrant_url)
    return QdrantVectorStore(
        client=client,
        collection_name=collection_name or settings.qdrant_collection,
        embedding=embeddings or get_embeddings(),
    )

# ===== Vanna Customization for Ollama =====

class OllamaVannaLLM:
    """A custom Vanna LLM backend using local Ollama API."""
    def __init__(self, config=None):
        self.model = settings.ollama_model
        self.base_url = settings.ollama_base_url
        
    def system_message(self, message: str) -> dict:
        return {"role": "system", "content": message}
        
    def user_message(self, message: str) -> dict:
        return {"role": "user", "content": message}
        
    def assistant_message(self, message: str) -> dict:
        return {"role": "assistant", "content": message}
        
    def submit_prompt(self, prompt: list[dict], **kwargs) -> str:
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "messages": prompt,
            "stream": False,
            "options": {
                "temperature": kwargs.get("temperature", 0.0)
            }
        }
        try:
            response = requests.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            return data.get("message", {}).get("content", "")
        except Exception as e:
            return f"Error communicating with Ollama: {str(e)}"
            
class MyVanna(OllamaVannaLLM, VannaQdrant_VectorStore):
    """Vanna that uses Qdrant as vector store and Ollama as LLM."""
    def __init__(self, config: Optional[Dict] = None):
        VannaBase.__init__(self)
        VannaQdrant_VectorStore.__init__(self, config=config)
        OllamaVannaLLM.__init__(self, config=config)
        
    def log(self, message, title="Info"):
        # Suppress verbose Vanna logs
        pass

    def generate_embedding(self, data: str) -> list[float]:
        embedding_model = get_embeddings()
        return embedding_model.embed_query(data)

def _pg_conn_kwargs_from_url(pg_url: str) -> Dict[str, Optional[str]]:
    """Parse a postgres URL into keyword args for connect_to_postgres."""
    parsed = urlparse(pg_url)
    return {
        "host": parsed.hostname,
        "dbname": parsed.path[1:] if parsed.path else None,
        "user": parsed.username,
        "password": parsed.password,
        "port": parsed.port,
    }

@lru_cache(maxsize=1)
def get_vanna(
    qdrant_url: Optional[str] = None,
    connect_postgres: bool = True,
    postgres_url: Optional[str] = None,
) -> MyVanna:
    """Return a cached MyVanna instance."""
    client = get_qdrant_client(url=qdrant_url)
    vn = MyVanna(config={"client": client})
    vn.allow_llm_to_see_data = True

    if connect_postgres:
        pg_url = postgres_url or settings.postgres_url
        if not pg_url:
            raise ValueError("Postgres URL is not provided or missing in settings.")
        conn_kwargs = _pg_conn_kwargs_from_url(pg_url)
        vn.connect_to_postgres(**conn_kwargs)

    return vn

@lru_cache(maxsize=1)
def get_graph_client() -> GraphClient:
    """Return a cached GraphClient instance."""
    return GraphClient(
        uri=settings.neo4j_uri,
        user=settings.neo4j_user,
        password=settings.neo4j_password,
    )
