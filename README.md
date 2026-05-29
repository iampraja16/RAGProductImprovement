# EMR Fault Analyzer (Hybrid RAG + SQL)

This is a production-ready AI agent for analyzing Equipment Maintenance Records (EMR). It uses a hybrid architecture combining semantic vector search (Qdrant) with structured data analytics (Vanna + PostgreSQL).

## Architecture

* **LLM**: Local Llama 3 (via Ollama)
* **Agent**: LangGraph (Tool-calling routing)
* **Vector DB**: Qdrant (Docker)
* **SQL DB**: PostgreSQL (Docker)
* **Backend**: FastAPI
* **Frontend**: Streamlit

## Setup Instructions

### 1. Prerequisites
- [Docker & Docker Compose](https://docs.docker.com/get-docker/)
- [Ollama](https://ollama.ai/)
- Python 3.10+

### 2. Install Dependencies
```bash
python -m venv venv
# Windows
venv\Scripts\activate
# Linux/Mac
source venv/bin/activate

pip install -r requirements.txt
```

### 3. Start Infrastructure
Start the vector and SQL databases using Docker:
```bash
cd docker
docker compose up -d
cd ..
```

Start the local LLM:
```bash
ollama pull llama3
ollama serve
```

### 4. Data Ingestion Pipeline
The ingestion is handled via Jupyter notebooks in the `notebook/` folder. You **must** run them in this order:

1. `notebook/1_clustering.ipynb` - Processes raw EMR Excel, runs UMAP+HDBSCAN, and labels clusters via Ollama.
2. `notebook/2_vector_ingestion.ipynb` - Embeds clustered text and generates summaries into Qdrant.
3. `notebook/3_sql_ingestion.ipynb` - Loads structured EMR data into PostgreSQL.
4. `notebook/4_vanna_training.ipynb` - Trains the Vanna SQL generator with schema and sample queries.

### 5. Start the Application

Start the FastAPI backend:
```bash
uvicorn src.main:app --reload
```

In a new terminal, start the Streamlit frontend:
```bash
streamlit run src/streamlit_app.py
```

Open your browser to `http://localhost:8501`.
