"""Service providers initialization (Replaces old utils.py)."""

from functools import lru_cache
from typing import Optional, Dict
from urllib.parse import urlparse
import requests

from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from langchain_ollama import ChatOllama

from src.config import settings
from src.services.embedding_service import EmbeddingService, embedding_svc
from src.graph.client import GraphClient

from vanna.base import VannaBase
from vanna.qdrant import Qdrant_VectorStore as VannaQdrant_VectorStore

@lru_cache(maxsize=1)
def get_embeddings() -> EmbeddingService:
    return embedding_svc

@lru_cache(maxsize=4)
def get_llm(temperature: float = 0.0) -> ChatOllama:
    return ChatOllama(
        model=settings.ollama_model,
        base_url=settings.ollama_base_url,
        temperature=temperature,
        num_ctx=4096,
    )

@lru_cache(maxsize=1)
def get_graph_client() -> GraphClient:
    return GraphClient(
        uri=settings.neo4j_uri,
        user=settings.neo4j_user,
        password=settings.neo4j_password,
    )

# --- Vanna SQL Analytics (Retained) ---

@lru_cache(maxsize=1)
def get_qdrant_client(url: Optional[str] = None) -> QdrantClient:
    target = url or settings.qdrant_url
    parsed = urlparse(target)
    host = parsed.hostname or "localhost"
    return QdrantClient(host=host, port=6333, grpc_port=6334, prefer_grpc=True)

class OllamaVannaLLM:
    def __init__(self, config=None):
        self.model = settings.ollama_model
        self.base_url = settings.ollama_base_url
        
    def system_message(self, message: str) -> dict: return {"role": "system", "content": message}
    def user_message(self, message: str) -> dict: return {"role": "user", "content": message}
    def assistant_message(self, message: str) -> dict: return {"role": "assistant", "content": message}
        
    def submit_prompt(self, prompt: list[dict], **kwargs) -> str:
        url = f"{self.base_url}/api/chat"
        payload = {"model": self.model, "messages": prompt, "stream": False, "options": {"temperature": kwargs.get("temperature", 0.0)}}
        try:
            response = requests.post(url, json=payload)
            response.raise_for_status()
            return response.json().get("message", {}).get("content", "")
        except Exception as e:
            return f"Error communicating with Ollama: {str(e)}"
            
class MyVanna(OllamaVannaLLM, VannaQdrant_VectorStore):
    def __init__(self, config: Optional[Dict] = None):
        VannaBase.__init__(self)
        VannaQdrant_VectorStore.__init__(self, config=config)
        OllamaVannaLLM.__init__(self, config=config)
        
    def log(self, message, title="Info"): pass

    def generate_embedding(self, data: str) -> list[float]:
        return get_embeddings().embed_query(data)

    def extract_sql(self, llm_response: str) -> str:
        # 1. Use Vanna's default extraction first (looks for ```sql)
        sql = super().extract_sql(llm_response)
        
        # 2. If it failed to clean (returned the raw string), do aggressive regex extraction
        if sql == llm_response:
            import re
            # Try to find SELECT ... ; (greedy up to last semicolon)
            match = re.search(r"(?i)(SELECT\s+.+?;)", sql, re.DOTALL)
            if not match:
                # Fallback: grab SELECT to end of string (no semicolon)
                match = re.search(r"(?i)(SELECT\s+.+)", sql, re.DOTALL)
            if match:
                sql = match.group(1)
                
        # 3. Final cleanup of any stray markdown, quotes, or trailing junk
        sql = sql.replace("```sql", "").replace("```", "").strip().strip('"').strip("'").strip()
        return sql

def _pg_conn_kwargs_from_url(pg_url: str) -> Dict[str, Optional[str]]:
    parsed = urlparse(pg_url)
    return {"host": parsed.hostname, "dbname": parsed.path[1:] if parsed.path else None, "user": parsed.username, "password": parsed.password, "port": parsed.port}

@lru_cache(maxsize=1)
def get_vanna(qdrant_url: Optional[str] = None, connect_postgres: bool = True, postgres_url: Optional[str] = None) -> MyVanna:
    client = get_qdrant_client(url=qdrant_url)
    vn = MyVanna(config={"client": client})
    vn.allow_llm_to_see_data = True
    if connect_postgres:
        pg_url = postgres_url or settings.postgres_url
        vn.connect_to_postgres(**_pg_conn_kwargs_from_url(pg_url))
    return vn
