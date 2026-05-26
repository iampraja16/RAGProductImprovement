import inspect
from pydantic import BaseModel, Field
from typing import List, Callable, Dict, Any, Type
import logging

from .utils import get_vector_store, get_vanna
from .config import settings
from .executive_summary import generate_summary

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
    Queries the structured EMR database using SQL.
    Use this tool ONLY for quantitative and analytical questions, such as:
    - Counts (e.g., berapa banyak, total, jumlah)
    - Trends over time
    - Grouping or rankings (e.g., top 5 site, model paling sering rusak)
    - Percentages or aggregations
    """
    logger.info(f"Using tool ask_emr_database for query: {query}")
    try:
        vn = get_vanna()
        sql = vn.generate_sql(query)
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
