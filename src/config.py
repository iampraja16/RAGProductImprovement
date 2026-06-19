import os
from dotenv import load_dotenv
from pydantic_settings import BaseSettings

_ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
load_dotenv(_ENV_FILE)


class Settings(BaseSettings):
    # ── LLM (Local Ollama) ───────────────────────────────────────────
    ollama_model: str = "qwen2.5:7b"
    ollama_base_url: str = "http://localhost:11434"

    # ── Embeddings ───────────────────────────────────────────────────
    embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2"
    embedding_dimension: int = 384   # paraphrase-multilingual-MiniLM-L12-v2 = 384

    # ── Vector Database (Qdrant) — dipertahankan untuk Vanna SQL ─────
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "emr_documents"

    # ── SQL Database (Postgres) ───────────────────────────────────────
    postgres_url: str = "postgresql://myuser:mypassword@localhost:5432/emr_db"

    # ── Graph Database (Neo4j) ────────────────────────────────────────
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "mypassword"

    # ── Neo4j Indexes ─────────────────────────────────────────────────
    # Vector indexes (dibuat oleh scripts/setup_indexes.py)
    neo4j_vector_index_symptom: str = "symptom-embeddings"
    neo4j_vector_index_community: str = "community-embeddings"
    # Fulltext indexes
    neo4j_fulltext_index_entity: str = "entity-names"

    # ── Graph Retrieval ───────────────────────────────────────────────
    graph_similarity_threshold: float = 0.65
    retriever_k: int = 8
    # Mode default untuk agent: "local" | "global" | "drift"
    default_retrieval_mode: str = "drift"
    # Jumlah kandidat hybrid search (vector + fulltext sebelum di-merge)
    hybrid_candidate_k: int = 20

    # ── Community Detection (GDS Leiden) ──────────────────────────────
    community_max_levels: int = 3
    community_gamma: float = 1.0          # Leiden resolution parameter
    community_theta: float = 0.01         # Leiden tolerance
    community_relationship_types: list = [
        "EXHIBITED", "CAUSED_BY", "RESOLVED_BY", "INVOLVES_PART", "BELONGS_TO", "MENTIONS"
    ]

    # ── Redis Cache ───────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── Data ──────────────────────────────────────────────────────────
    data_dir: str = "data"
    emr_file_name: str = "Dashboard EMR(report1776669858353).csv"
    emr_sheet_name: str = "report1776669858353"

    class Config:
        env_file = _ENV_FILE
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
