import os
from dotenv import load_dotenv
from pydantic_settings import BaseSettings

_ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
load_dotenv(_ENV_FILE)

class Settings(BaseSettings):
    # LLM (Local Ollama)
    # BEFORE: ollama_model: str = "llama3.1"
    ollama_model: str = "qwen2.5:7b"
    ollama_base_url: str = "http://localhost:11434"

    # Embeddings
    embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2"

    # Vector Database (Qdrant)
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "emr_documents"

    # SQL Database (Postgres)
    postgres_url: str = "postgresql://myuser:mypassword@localhost:5432/emr_db"

    # Graph Database (Neo4j)
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "mypassword"
    graph_similarity_threshold: float = 0.65

    # Redis Cache (FASE 2)
    redis_url: str = "redis://localhost:6379/0"

    # Retriever
    retriever_k: int = 8

    # Data
    data_dir: str = "data"
    emr_file_name: str = "Dashboard EMR(report1776669858353).csv"
    emr_sheet_name: str = "report1776669858353"

    # Clustering
    hdbscan_min_cluster_size: int = 5
    hdbscan_min_samples: int = 3
    umap_n_components: int = 10
    umap_n_neighbors: int = 15

    class Config:
        env_file = _ENV_FILE
        env_file_encoding = "utf-8"
        extra = "ignore"

settings = Settings()
