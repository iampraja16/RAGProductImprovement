import inspect
from pydantic import BaseModel, Field
from typing import List, Callable, Dict, Any, Type
import logging

from .utils import get_vector_store, get_vanna, get_graph_client, get_embeddings
from .config import settings
from .executive_summary import generate_summary
from .graph_client import format_graph_result

logger = logging.getLogger(__name__)

# ===== Tool Registry Pattern =====

REGISTERED_TOOLS: List[Callable] = []

def register_tool(args_schema: Type[BaseModel] = None):
    """Decorator to register a tool function for the agent."""
    def decorator(func):
        if args_schema:
            func.args_schema = args_schema
        REGISTERED_TOOLS.append(func)
        return func
    return decorator

def get_registered_tools() -> List[Callable]:
    """Returns the list of all registered tools."""
    return REGISTERED_TOOLS

def get_tool_schemas() -> List[Dict[str, Any]]:
    """Generates OpenAI-compatible tool schemas for registered tools."""
    schemas = []
    for tool in REGISTERED_TOOLS:
        name = tool.__name__
        description = inspect.getdoc(tool) or ""
        
        parameters = {"type": "object", "properties": {}, "required": []}
        if hasattr(tool, "args_schema") and tool.args_schema:
            schema = tool.args_schema.model_json_schema()
            parameters["properties"] = schema.get("properties", {})
            parameters["required"] = schema.get("required", [])

        schemas.append({
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            }
        })
    return schemas

# ===== Pydantic Schemas for Tools =====

class QueryArgs(BaseModel):
    query: str = Field(description="The user's query or question.")

class ReportArgs(BaseModel):
    family: str = Field(description="The model family to generate the report for, e.g., 'PC200', 'HD465'.")

# ===== Tool Implementations =====

@register_tool(args_schema=QueryArgs)
def ask_emr_knowledge(query: str) -> Dict[str, Any]:
    """
    Searches the unstructured EMR knowledge base.
    Use this tool ONLY for qualitative questions, such as asking for:
    - Root causes (penyebab utama)
    - Symptoms (gejala)
    - Descriptions of specific damage
    - Maintenance procedures
    DO NOT use this for counting or calculating data.
    """
    logger.info(f"Using tool ask_emr_knowledge for query: {query}")
    try:
        store = get_vector_store()
        docs = store.similarity_search(query, k=settings.retriever_k)
        
        chunks = [doc.page_content for doc in docs]

        # FASE 3: Compact context + hard truncation
        from .prompt import format_compact_context
        context = format_compact_context("", vector_chunks=chunks)

        return {
            "answer": context,
            "chunks": chunks,
            "sql": None
        }
    except Exception as e:
        logger.error(f"Error in ask_emr_knowledge: {e}")
        return {"answer": f"Error retrieving knowledge: {e}", "chunks": [], "sql": None}

@register_tool(args_schema=QueryArgs)
def ask_emr_database(query: str) -> Dict[str, Any]:
    """
    Queries the structured EMR database.
    The 'query' argument MUST be the original user's question in natural language (Bahasa Indonesia/Inggris).
    DO NOT generate or pass a SQL query as the argument. Pass the raw question as is.
    Use this tool ONLY for quantitative and analytical questions, such as:
    - Counts (e.g., berapa banyak, total, jumlah)
    - Trends over time
    - Grouping or rankings (e.g., top 5 site, model paling sering rusak)
    - Percentages or aggregations
    """
    logger.info(f"Using tool ask_emr_database for query: {query}")
    try:
        vn = get_vanna()
        sql = vn.generate_sql(query, allow_llm_to_see_data=True)
        logger.info(f"Generated SQL: {sql}")
        
        if not sql:
            return {"answer": "Could not generate SQL for the query.", "chunks": None, "sql": None}
            
        df = vn.run_sql(sql)
        
        if df is None or df.empty:
            result_str = "No data returned from database."
            sql_data = []
        else:
            result_str = df.to_markdown(index=False)
            # Replace NaN and Inf values with None/0 to ensure valid JSON serialization
            df_cleaned = df.replace({float('nan'): None, float('inf'): None, float('-inf'): None})
            sql_data = df_cleaned.to_dict(orient="records")
            
        return {
            "answer": result_str,
            "chunks": None,
            "sql": sql,
            "sql_data": sql_data
        }
    except Exception as e:
        logger.error(f"Error in ask_emr_database: {e}")
        return {"answer": f"Error querying database: {e}", "chunks": None, "sql": None, "sql_data": None}

@register_tool(args_schema=QueryArgs)
def ask_emr_graph(query: str) -> Dict[str, Any]:
    """
    Searches the EMR knowledge graph for causal relationships between symptoms and solutions.
    Use this tool when the user describes a symptom or problem and asks for:
    - Recommended solutions or corrective actions (solusi, tindakan, apa yang harus dilakukan)
    - Historical fixes for similar problems (bagaimana cara perbaiki, fix)
    - Parts needed for a specific repair
    - Relationship between symptoms, problems, and corrective actions
    DO NOT use this for counting data (use ask_emr_database) or open-ended explanations (use ask_emr_knowledge).
    """
    logger.info(f"Using tool ask_emr_graph for query: {query}")
    try:
        # FASE 3: Import parallelization utilities
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
        from .prompt import format_compact_context, truncate_to_tokens, estimate_tokens
        import numpy as np

        graph = get_graph_client()
        embeddings = get_embeddings()
        query_embedding = embeddings.embed_query(query)
        query_emb = np.array(query_embedding)

        store = get_vector_store()

        # ---------------------------------------------------------------
        # FASE 3: PARALLEL retrieval with 5s timeout per step
        # BEFORE: Sequential — graph first, then vector only on cold start
        # AFTER:  Concurrent — graph + vector run simultaneously
        # ---------------------------------------------------------------
        graph_result = None
        vector_docs = []

        with ThreadPoolExecutor(max_workers=2) as executor:
            graph_future = executor.submit(
                graph.find_solutions_for_symptom, query_emb
            )
            vector_future = executor.submit(
                store.similarity_search, query, settings.retriever_k
            )

            # Collect graph result (5s timeout)
            try:
                graph_result = graph_future.result(timeout=5)
            except (FutureTimeout, Exception) as e:
                logger.warning("Graph search timeout/error (5s): %s", e)
                graph_result = {
                    "cold_start": True,
                    "message": f"Graph query timed out ({e})",
                }

            # Collect vector result (5s timeout)
            try:
                vector_docs = vector_future.result(timeout=5)
            except (FutureTimeout, Exception) as e:
                logger.warning("Vector search timeout/error (5s): %s", e)
                vector_docs = []

        chunks = [doc.page_content for doc in vector_docs]

        # ---------------------------------------------------------------
        # Assemble results
        # ---------------------------------------------------------------
        if graph_result.get("cold_start"):
            logger.info("Cold start — using vector results as primary.")
            context = format_compact_context("", vector_chunks=chunks)

            fallback_msg = (
                f"[COLD START] {graph_result.get('message', '')}\n"
                f"Nearest: {graph_result.get('best_guess', 'N/A')} "
                f"(sim: {graph_result.get('similarity', 0):.2f})\n\n"
                f"{context}"
            )
            # FASE 3: Hard truncation
            fallback_msg = truncate_to_tokens(fallback_msg)

            return {
                "answer": fallback_msg,
                "chunks": chunks,
                "sql": None,
                "graph_traversal": None,
            }

        # Normal graph result — combine with vector context
        formatted_graph = format_graph_result(graph_result)
        combined = format_compact_context(formatted_graph, vector_chunks=chunks)

        # FASE 3: Hard truncation + token warning
        token_count = estimate_tokens(combined)
        if token_count > 1600:
            logger.warning("Context tokens (%d) exceeds 1600, truncating.", token_count)
            combined = truncate_to_tokens(combined)

        return {
            "answer": combined,
            "chunks": chunks,
            "sql": None,
            "graph_traversal": graph_result.get("traversal_path"),
        }
    except Exception as e:
        logger.error(f"Error in ask_emr_graph: {e}")
        return {"answer": f"Error querying knowledge graph: {e}", "chunks": None, "sql": None, "graph_traversal": None}

@register_tool(args_schema=ReportArgs)
def generate_executive_summary(family: str) -> Dict[str, Any]:
    """
    Generates an executive summary HTML/PDF report for a specific model family.
    Use this tool when the user explicitly asks to "generate report", "buat laporan", 
    or "buat executive summary" for a model family.
    """
    logger.info(f"Using tool generate_executive_summary for family: {family}")
    try:
        # Generate the report (returns bytes for PDF, string for HTML)
        # We assume the executive_summary module saves the files and returns the content
        pdf_bytes, html_content = generate_summary(family, use_llm=True)
        
        if not pdf_bytes or not html_content:
            return {"answer": f"Failed to generate report for {family}.", "chunks": None, "sql": None}
            
        return {
            "answer": f"Executive summary for {family} has been successfully generated. The report contains clustered insights and failure analysis.",
            "chunks": None,
            "sql": None
        }
    except Exception as e:
        logger.error(f"Error in generate_executive_summary: {e}")
        return {"answer": f"Error generating report: {e}", "chunks": None, "sql": None}

