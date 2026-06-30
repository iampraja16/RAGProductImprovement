import os
from typing import Optional
from dotenv import load_dotenv
from pydantic_settings import BaseSettings

_ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
load_dotenv(_ENV_FILE)


class Settings(BaseSettings):
    env: str = "development"
    api_key: str = ""
    model_provider: str = "azure"

    azure_openai_api_key: Optional[str] = None
    azure_openai_endpoint: Optional[str] = None
    azure_openai_llm_deployment_name: str = "TC-gpt-5.4-mini"
    azure_openai_mini_deployment_name: str = "TC-gpt-5.4-mini"
    azure_openai_api_version: str = "2024-12-01-preview"
    azure_openai_embed_model_deployment_name: str = "TC-text-embedding-3-small"

    azure_openai_failover_endpoint: Optional[str] = None
    azure_openai_failover_api_key: Optional[str] = None

    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o"
    openai_mini_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"

    embedding_dimension: int = 1536

    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "emr_documents"

    postgres_url: str = "postgresql://myuser:mypassword@localhost:5432/emr_db"
    readonly_postgres_url: str = "postgresql://readonly_user:readonly_password@localhost:5432/emr_db"
    sql_row_limit: int = 100
    dataframe_markdown_limit: int = 20
    dataframe_summary_token_limit: int = 1000

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "mypassword"

    neo4j_vector_index_symptom: str = "symptom-embeddings"
    neo4j_vector_index_community: str = "community-embeddings"
    neo4j_fulltext_index_entity: str = "entity-names"

    community_gamma: float = 1.0
    community_theta: float = 0.01
    community_relationship_types: list = [
        "EXHIBITED", "CAUSED_BY", "RESOLVED_BY", "INVOLVES_PART", "BELONGS_TO", "MENTIONS", "ON_MACHINE"
    ]

    redis_url: str = "redis://localhost:6379/0"

    eval_model_family: str = "gpt-4o"
    
    otel_enabled: bool = False
    otel_service_name: str = "emr-fault-analyzer"
    otel_exporter_endpoint: Optional[str] = None

    data_dir: str = "data"
    emr_file_name: str = "Dashboard EMR(report1776669858353).csv"
    emr_sheet_name: str = "report1776669858353"

    class Config:
        env_file = _ENV_FILE
        env_file_encoding = "utf-8"
        extra = "ignore"

settings = Settings()