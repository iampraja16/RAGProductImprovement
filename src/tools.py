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
        context = "\n\n".join([f"Document {i+1}:\n{chunk}" for i, chunk in enumerate(chunks)])
        
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
        else:
            result_str = df.to_markdown(index=False)
            
        return {
            "answer": result_str,
            "chunks": None,
            "sql": sql
        }
    except Exception as e:
        logger.error(f"Error in ask_emr_database: {e}")
        return {"answer": f"Error querying database: {e}", "chunks": None, "sql": None}

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
        graph = get_graph_client()
        embeddings = get_embeddings()
        query_embedding = embeddings.embed_query(query)
        
        import numpy as np
        query_emb = np.array(query_embedding)
        
        result = graph.find_solutions_for_symptom(query_emb)
        
        if result.get("cold_start"):
            # Fallback to Qdrant vector search
            logger.info(f"Cold start detected (similarity: {result.get('similarity', 0):.2f}). Falling back to Qdrant.")
            store = get_vector_store()
            docs = store.similarity_search(query, k=settings.retriever_k)
            chunks = [doc.page_content for doc in docs]
            context = "\n\n".join(chunks[:5])
            
            fallback_msg = (
                f"[COLD START] {result.get('message', '')}\n\n"
                f"Gejala terdekat yang tercatat: **{result.get('best_guess', 'N/A')}** "
                f"(kecocokan: {result.get('similarity', 0):.0%})\n\n"
                f"Berikut data historis terkait dari pencarian semantik:\n\n{context}"
            )
            
            return {
                "answer": fallback_msg,
                "chunks": chunks,
                "sql": None,
                "graph_traversal": None
            }
        
        # Normal graph result
        formatted = format_graph_result(result)
        return {
            "answer": formatted,
            "chunks": None,
            "sql": None,
            "graph_traversal": result.get("traversal_path")
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

