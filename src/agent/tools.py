"""Agent tools (Refactored to use new retrieval architecture)."""

import logging
from typing import Dict, Any, List, Type
from pydantic import BaseModel, Field
import inspect

from src.services.providers import get_vanna, get_graph_client, get_embeddings, get_llm
from src.graph.retrieval.local import LocalSearchRetriever
from src.graph.retrieval.global_search import GlobalSearchRetriever
from src.graph.retrieval.drift import DriftSearchRetriever
from src.agent.prompts import estimate_tokens, truncate_to_tokens

logger = logging.getLogger(__name__)

# ===== Tool Registry Pattern =====

REGISTERED_TOOLS: List[Any] = []

def register_tool(args_schema: Type[BaseModel] = None):
    def decorator(func):
        if args_schema:
            func.args_schema = args_schema
        REGISTERED_TOOLS.append(func)
        return func
    return decorator

def get_registered_tools() -> List[Any]:
    return REGISTERED_TOOLS

def get_tool_schemas() -> List[Dict[str, Any]]:
    schemas = []
    for tool in REGISTERED_TOOLS:
        name = tool.__name__
        description = inspect.getdoc(tool) or ""
        parameters = {"type": "object", "properties": {}, "required": []}
        if hasattr(tool, "args_schema") and tool.args_schema:
            schema = tool.args_schema.model_json_schema()
            parameters["properties"] = schema.get("properties", {})
            parameters["required"] = schema.get("required", [])
        schemas.append({"type": "function", "function": {"name": name, "description": description, "parameters": parameters}})
    return schemas

# ===== Pydantic Schemas =====

class QueryArgs(BaseModel):
    query: str = Field(description="The user's query or question.")

class ReportArgs(BaseModel):
    family: str = Field(description="The model family to generate the report for, e.g., 'PC200', 'HD465'.")

class GraphRetrievalArgs(BaseModel):
    query: str = Field(description="The user's query or question.")
    mode: str = Field(default="drift", description="Retrieval mode: 'local', 'global', or 'drift'")

# ===== Tool Implementations =====

@register_tool(args_schema=GraphRetrievalArgs)
def ask_emr_graph(query: str, mode: str = "drift") -> Dict[str, Any]:
    """
    Searches the EMR knowledge graph. 
    Use 'local' for specific symptoms/parts.
    Use 'global' for broad trends and landscape questions.
    Use 'drift' (default) for detailed questions that also need broad context.
    """
    logger.info(f"Using tool ask_emr_graph (mode: {mode}) for query: {query}")
    try:
        gc = get_graph_client()
        llm = get_llm()
        embedder = get_embeddings()

        if mode == "local":
            retriever = LocalSearchRetriever(gc, llm, embedder)
        elif mode == "global":
            retriever = GlobalSearchRetriever(gc, llm, embedder)
        else:
            retriever = DriftSearchRetriever(gc, llm, embedder)

        result = retriever.search(query)
        
        answer = result.answer
        if estimate_tokens(answer) > 2000:
            answer = truncate_to_tokens(answer, 2000)

        return {
            "answer": answer,
            "chunks": [],
            "sql": None,
            "graph_traversal": result.graph_context
        }
    except Exception as e:
        logger.error(f"Error in ask_emr_graph: {e}")
        return {"answer": f"Error querying knowledge graph: {e}", "chunks": [], "sql": None, "graph_traversal": None}

@register_tool(args_schema=QueryArgs)
def ask_emr_database(query: str) -> Dict[str, Any]:
    """
    Queries the structured EMR database.
    Use this tool ONLY for quantitative and analytical questions (e.g., how many, trends over time, top 5).
    """
    logger.info(f"Using tool ask_emr_database for query: {query}")
    try:
        vn = get_vanna()
        sql = vn.generate_sql(query, allow_llm_to_see_data=True)
        
        if not sql:
            return {"answer": "Could not generate SQL for the query.", "chunks": None, "sql": None}
            
        df = vn.run_sql(sql)
        if df is None or df.empty:
            result_str = "No data returned from database."
            sql_data = []
        else:
            result_str = df.to_markdown(index=False)
            df_cleaned = df.replace({float('nan'): None, float('inf'): None, float('-inf'): None})
            sql_data = df_cleaned.to_dict(orient="records")
            
        return {"answer": result_str, "chunks": None, "sql": sql, "sql_data": sql_data}
    except Exception as e:
        logger.error(f"Error in ask_emr_database: {e}")
        return {"answer": f"Error querying database: {e}", "chunks": None, "sql": None, "sql_data": None}

@register_tool(args_schema=ReportArgs)
def generate_executive_summary(family: str) -> Dict[str, Any]:
    """
    Generates an executive summary HTML/PDF report for a specific model family.
    """
    logger.info(f"Using tool generate_executive_summary for family: {family}")
    try:
        from src.executive_summary import generate_summary
        pdf_bytes, html_content = generate_summary(family, use_llm=True)
        if not pdf_bytes or not html_content:
            return {"answer": f"Failed to generate report for {family}."}
        return {"answer": f"Executive summary for {family} has been successfully generated."}
    except Exception as e:
        logger.error(f"Error in generate_executive_summary: {e}")
        return {"answer": f"Error generating report: {e}"}
