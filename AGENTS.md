# AGENTS.md — Local RAG Comparator (EMR Fault Analyzer)

## Project Overview
Production-grade Hybrid GraphRAG + SQL system for Equipment Maintenance Records (EMR) analysis.
- **Backend**: FastAPI + LangGraph agent (Azure OpenAI / OpenAI)
- **Frontend**: Streamlit
- **Graph DB**: Neo4j 5 (GDS Leiden clustering, APOC)
- **Vector DB**: Qdrant
- **SQL DB**: PostgreSQL + pgvector (Vanna AI Text-to-SQL)
- **Cache**: Redis (semantic + embedding cache)
- **Observability**: OpenTelemetry (optional), structured JSON logging

---

## Quick Start Commands

```bash
# 1. Environment setup
cp .env.example .env
# Edit .env with Azure OpenAI keys, DB passwords

# 2. Python deps
python -m venv venv
source venv/bin/activate  # Windows: .\venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 3. Infrastructure (Neo4j, PostgreSQL, Qdrant, Redis)
cd docker && docker compose up -d && cd ..

# 4. Neo4j indexes (required before ingestion)
python scripts/setup_indexes.py

# 5. Data ingestion pipeline (run notebooks in order)
# notebook/1_sql_ingestion.ipynb → 2_graph_extraction.ipynb → 3_entity_resolution.ipynb
# → 4_community_pipeline.ipynb → 5_graph_to_sql_sync.ipynb → 6_vanna_training.ipynb

# 6. Run services
uvicorn src.main:app --reload          # Backend on :8000
streamlit run src/streamlit_app.py     # Frontend on :8501
```

---

## Key Commands

| Task | Command |
|------|---------|
| Run all tests | `python -m unittest discover -s tests` |
| Run single test file | `python -m unittest tests.test_agent_tools` |
| Run evaluation | `python eval/run_eval.py` |
| Sync Graph↔SQL (bidirectional) | `python scripts/sync_graph_to_sql.py [--dry-run]` |
| Create read-only PG user | `python scripts/create_readonly_user.py` |
| Setup Neo4j indexes | `python scripts/setup_indexes.py` |

---

## Architecture Notes (Non-Obvious)

1. **Dual-DB Sync**: Neo4j (graph) ↔ PostgreSQL (SQL) are kept in sync via `scripts/sync_graph_to_sql.py` using `community_id` arrays on `emr_records`. Run after any graph changes.

2. **Entity Resolution is Mandatory**: All user queries pass through `EntityResolver` (`src/services/entity_resolver.py`) which maps free-text mentions → canonical Neo4j names + `community_id` via fulltext + vector search. SQL queries inject `community_id` filters.

3. **Agent Tools** (`src/agent/tools.py`):
   - `ask_emr_graph` — GraphRAG (local/global/drift modes)
   - `ask_emr_database` — Text-to-SQL via Vanna (SQL sandboxed, LIMIT injected, ILIKE fallback)
   - `search_emr_records` — Direct EMR lookup via graph traversal
   - `generate_executive_summary` — PDF report generation

4. **Circuit Breakers** (`src/services/resilience.py`): Separate breakers for Neo4j, PostgreSQL, Qdrant, Cloud LLM (primary + failover). HTTP 429 does NOT trip breaker; 5xx does.

5. **SQL Safety**: `_is_safe_select_query()` in `tools.py` blocks mutations, multi-statements, DDL. Only `SELECT`/`WITH` allowed. Limit auto-injected.

6. **Provenance Required**: Synthesizer prompt (`src/agent/prompts.py`) forces `--- EVIDENCE/PROVENANCE ---` divider with record identifiers. UI splits on this.

7. **Vanna Training Artifacts** (`vanna_training/`): `schema.sql`, `qa_pairs.yaml`, `domain_docs.md` must be valid — validated by `tests/test_vanna_training.py`.

---

## Testing Notes

- **Framework**: `unittest` (stdlib)
- **Run all**: `python -m unittest discover -s tests`
- **Test files** (no redundancy):
  - `test_agent_tools.py` — Provenance divider, SQL sandbox, LIMIT injection
  - `test_api_endpoints.py` — FastAPI routing, auth, `/health`
  - `test_resilience_circuit.py` — Circuit breaker state transitions, thread-safety
  - `test_data_pipeline.py` — Sync idempotency, dry-run, rollback
  - `test_eval_utils.py` — Atomic file writes
  - `test_vanna_training.py` — Training artifact integrity
- **No pytest**, no fixtures, no external test DB — tests mock providers.

---

## Environment Variables (`.env`)

`.env` is now cleaned and matches `config.py` fields. Use `.env.example` as template:

Required for production/staging:
```
MODEL_PROVIDER=azure
AZURE_OPENAI_ENDPOINT=...
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_API_VERSION=2024-12-01-preview
AZURE_OPENAI_LLM_DEPLOYMENT_NAME=TC-gpt-5.4-mini
AZURE_OPENAI_MINI_DEPLOYMENT_NAME=TC-gpt-5.4-mini
AZURE_OPENAI_EMBED_MODEL_DEPLOYMENT_NAME=TC-text-embedding-3-small-2
POSTGRES_URL=postgresql://user:pass@localhost:5432/emr_db
READONLY_POSTGRES_URL=postgresql://ro_user:ro_pass@localhost:5432/emr_db
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=...
QDRANT_URL=http://localhost:6333
REDIS_URL=redis://localhost:6379/0
API_KEY=...  # Required if ENV=staging/production
```

Optional failover:
```
AZURE_OPENAI_FAILOVER_ENDPOINT=...
AZURE_OPENAI_FAILOVER_API_KEY=...
```

OpenAI fallback (non-Azure):
```
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o
OPENAI_MINI_MODEL=gpt-4o-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
```

Observability:
```
OTEL_ENABLED=true
OTEL_EXPORTER_ENDPOINT=...
```

**Note**: `config.py` expects `AZURE_OPENAI_LLM_DEPLOYMENT_NAME` etc., NOT `AZURE_OPENAI_DEPLOYMENT_NAME` (used in eval script).

---

## Common Gotchas

| Issue | Cause / Fix |
|-------|-------------|
| Neo4j connection fails | Ensure GDS + APOC plugins enabled (`NEO4J_PLUGINS=["apoc","graph-data-science"]` in docker-compose) |
| Vanna SQL generation fails | Check `vanna_training/` files valid; run `test_vanna_training.py` |
| Graph↔SQL sync shows 0 rows | Run `setup_indexes.py` first; ensure `community_id` exists on `EMRRecord` nodes |
| API returns 403 | Set `API_KEY` in `.env` and pass `X-API-Key` header (required in staging/prod) |
| Streamlit can't connect | Backend must be running on `http://localhost:8000`; check `API_URL` env |
| Greenlet build error on Windows | `greenlet>=3.0.0,<3.2.0` pinned in requirements |

---

## File Structure (Key Entrypoints)

```
src/
├── main.py                 # FastAPI app, /chat, /health, /cache/*
├── streamlit_app.py        # Streamlit UI
├── config.py               # Pydantic Settings (all env vars)
├── agent/
│   ├── agent.py           # LangGraph agent (router → tool → synthesizer)
│   ├── tools.py           # 4 registered tools + SQL sandbox
│   └── prompts.py         # Router & synthesizer prompts
├── services/
│   ├── providers.py       # Cached singletons: LLM, Graph, Qdrant, Vanna
│   ├── entity_resolver.py # Free-text → canonical + community_id
│   ├── resilience.py      # Circuit breakers + retry logic
│   ├── cache_service.py   # Semantic (Qdrant) + Redis cache
│   └── embedding_service.py
├── graph/
│   ├── client.py          # Neo4j driver wrapper
│   ├── retrieval/         # Local, Global, Drift, Hybrid retrievers
│   └── index_manager.py   # Index creation (vector, fulltext)
├── ingestion/             # CSV → Neo4j/Postgres pipeline
└── community/             # Leiden clustering + LLM summarization
```

---

## Evaluation

Golden QA dataset: `eval/golden_qa.jsonl` (query, expected_answer, category)
Run: `python eval/run_eval.py` → outputs metrics to `eval/results/`

Atomic file writes (`save_atomic_json`, `save_atomic_text`) prevent corruption on crash.

---

## Deployment Notes

- **API Key enforcement**: Only in `staging`/`production` env (`settings.env`)
- **Read-only PG user**: Created via `scripts/create_readonly_user.py` for Vanna
- **Resource limits**: Defined in `docker/docker-compose.yml` (memory/CPU per service)
- **Observability**: Set `OTEL_ENABLED=true` + `OTEL_EXPORTER_ENDPOINT` for tracing