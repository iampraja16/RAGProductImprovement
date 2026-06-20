import os
from typing import Optional
from dotenv import load_dotenv
from pydantic_settings import BaseSettings

_ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
load_dotenv(_ENV_FILE)


class Settings(BaseSettings):
    # ── Environment & Security ───────────────────────────────────────
    env: str = "development"
    api_key: str = ""

    # ── LLM Provider Selection ────────────────────────────────────────
    # "azure"  → AzureChatOpenAI (requires AZURE_OPENAI_* vars)
    # "openai" → ChatOpenAI     (requires OPENAI_API_KEY)
    llm_provider: str = "azure"

    # ── Azure OpenAI (Primary — Cloud) ───────────────────────────────
    azure_openai_api_key: Optional[str] = None
    azure_openai_endpoint: Optional[str] = None
    azure_openai_deployment_name: str = "gpt-4o"
    azure_openai_mini_deployment_name: str = "gpt-4o-mini"
    azure_openai_api_version: str = "2024-02-01"
    azure_openai_embedding_deployment: str = "text-embedding-3-small"

    # ── Azure OpenAI (Secondary / Failover Region) ───────────────────
    azure_openai_failover_endpoint: Optional[str] = None
    azure_openai_failover_api_key: Optional[str] = None

    # ── OpenAI API (Fallback — non-Azure) ────────────────────────────
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o"
    openai_mini_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"

    # ── Embeddings ───────────────────────────────────────────────────
    # embedding_dimension is driven by the active provider:
    #   SentenceTransformer (local, DEPRECATED) = 384
    #   text-embedding-3-small / text-embedding-ada-002 (cloud) = 1536
    embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2"  # DEPRECATED
    embedding_dimension: int = 1536  # Updated for cloud embedding model

    # ── Vector Database (Qdrant) ──────────────────────────────────────
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "emr_documents"

    # ── SQL Database (Postgres) ───────────────────────────────────────
    postgres_url: str = "postgresql://myuser:mypassword@localhost:5432/emr_db"
    readonly_postgres_url: str = "postgresql://readonly_user:readonly_password@localhost:5432/emr_db"
    sql_row_limit: int = 100
    dataframe_markdown_limit: int = 20
    dataframe_summary_token_limit: int = 1000

    # ── Graph Database (Neo4j) ────────────────────────────────────────
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "mypassword"

    # ── Neo4j Indexes ─────────────────────────────────────────────────
    neo4j_vector_index_symptom: str = "symptom-embeddings"
    neo4j_vector_index_community: str = "community-embeddings"
    neo4j_fulltext_index_entity: str = "entity-names"

    # ── Graph Retrieval ───────────────────────────────────────────────
    graph_similarity_threshold: float = 0.65
    retriever_k: int = 8
    default_retrieval_mode: str = "drift"
    hybrid_candidate_k: int = 20

    # ── Community Detection (GDS Leiden) ──────────────────────────────
    community_max_levels: int = 3
    community_gamma: float = 1.0
    community_theta: float = 0.01
    community_relationship_types: list = [
        "EXHIBITED", "CAUSED_BY", "RESOLVED_BY", "INVOLVES_PART", "BELONGS_TO", "MENTIONS"
    ]

    # ── Redis Cache ───────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── Evaluation ────────────────────────────────────────────────────
    eval_model_family: str = "gpt-4o"
    
    # ── Telemetry / Observability ─────────────────────────────────────
    otel_enabled: bool = False
    otel_service_name: str = "emr-fault-analyzer"
    otel_exporter_endpoint: Optional[str] = None

    # ── Data ──────────────────────────────────────────────────────────
    data_dir: str = "data"
    emr_file_name: str = "Dashboard EMR(report1776669858353).csv"
    emr_sheet_name: str = "report1776669858353"

    class Config:
        env_file = _ENV_FILE
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
