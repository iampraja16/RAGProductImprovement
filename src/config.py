from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # LLM (Local Ollama)
    ollama_model: str = "llama3"
    ollama_base_url: str = "http://localhost:11434"

    # Embeddings
    embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2"

    # Vector Database (Qdrant)
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "emr_documents"

    # SQL Database (Postgres)
    postgres_url: str = "postgresql://myuser:mypassword@localhost:5432/emr_db"

    # Retriever
    retriever_k: int = 15

    # Data
    data_dir: str = "data"
    emr_file_name: str = "Dashboard EMR.xlsx"
    emr_sheet_name: str = "report1776669858353"

    # Clustering
    hdbscan_min_cluster_size: int = 5
    hdbscan_min_samples: int = 3
    umap_n_components: int = 10
    umap_n_neighbors: int = 15

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
