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

import re
from src.config import settings

def _is_safe_select_query(sql: str) -> bool:
    # 1. Remove comments
    sql_clean = re.sub(r'--.*$', '', sql, flags=re.MULTILINE)
    sql_clean = re.sub(r'/\*.*?\*/', '', sql_clean, flags=re.DOTALL)
    sql_clean = sql_clean.strip()
    
    # 2. Remove string literals to avoid matching keywords inside text
    sql_no_strings = re.sub(r"'[^']*(?:''[^']*)*'", "''", sql_clean)
    
    # 3. Check start pattern (SELECT or WITH)
    pattern = r'^(?:\s*\(?)*\s*(?:SELECT|WITH)\b'
    if not re.match(pattern, sql_no_strings, re.IGNORECASE):
        return False
        
    # 4. Check for forbidden keywords (case-insensitive)
    forbidden_keywords = [
        r'\bINSERT\b', r'\bUPDATE\b', r'\bDELETE\b', r'\bDROP\b', r'\bALTER\b',
        r'\bCREATE\b', r'\bTRUNCATE\b', r'\bREPLACE\b', r'\bGRANT\b', r'\bREVOKE\b',
        r'\bCOPY\b', r'\bMERGE\b', r'\bCALL\b', r'\bEXECUTE\b', r'\bDO\b',
        r'\bVACUUM\b', r'\bANALYZE\b'
    ]
    for kw_pattern in forbidden_keywords:
        if re.search(kw_pattern, sql_no_strings, re.IGNORECASE):
            return False
            
    # 5. Check for multiple statements (semicolon not at the end)
    sql_check = sql_no_strings.strip()
    if sql_check.endswith(';'):
        sql_check = sql_check[:-1].rstrip()
    if ';' in sql_check:
        return False
            
    return True

def _inject_limit_if_missing(sql: str, default_limit: int) -> str:
    sql_strip = sql.strip()
    
    # Strip trailing semicolon if present
    has_semicolon = sql_strip.endswith(';')
    if has_semicolon:
        sql_strip = sql_strip[:-1].rstrip()
        
    # Remove string literals to check for LIMIT keyword
    sql_no_strings = re.sub(r"'[^']*(?:''[^']*)*'", "''", sql_strip)
    
    # Check if LIMIT is present
    if not re.search(r'\bLIMIT\b', sql_no_strings, re.IGNORECASE):
        sql_strip = f"{sql_strip} LIMIT {default_limit}"
        
    if has_semicolon:
        sql_strip = f"{sql_strip};"
        
    return sql_strip

@register_tool(args_schema=QueryArgs)
def ask_emr_database(query: str) -> Dict[str, Any]:
    """
    Queries the structured EMR database.
    Use this tool ONLY for quantitative and analytical questions (e.g., how many, trends over time, top 5).
    """
    logger.info(f"Using tool ask_emr_database for query: {query}")
    try:
        vn = get_vanna()
        sql = vn.generate_sql(query, allow_llm_to_see_data=False)
        
        if not sql:
            return {"answer": "Could not generate SQL for the query.", "chunks": None, "sql": None, "sql_data": None}
            
        # Sandbox SQL Check
        if not _is_safe_select_query(sql):
            logger.warning(f"Blocked unsafe or non-SELECT query generated by LLM: {sql}")
            return {"answer": "Blocked unsafe or non-SELECT query generated by LLM.", "chunks": None, "sql": sql, "sql_data": None}
            
        # Limit Injection
        sql = _inject_limit_if_missing(sql, settings.sql_row_limit)
        logger.info(f"Executing sandboxed SQL: {sql}")
        
        df = vn.run_sql(sql)
        if df is None or df.empty:
            result_str = "No data returned from database."
            sql_data = []
        else:
            markdown_table = df.to_markdown(index=False)
            df_cleaned = df.replace({float('nan'): None, float('inf'): None, float('-inf'): None})
            sql_data = df_cleaned.to_dict(orient="records")
            
            # Extract record identifiers (e.g., values of emr_name, model_name, etc.)
            record_identifiers = []
            for col in ["emr_name", "model_name", "symptom", "name", "component", "model"]:
                if col in df.columns:
                    record_identifiers.extend(df[col].dropna().unique().astype(str).tolist())
            
            # Extract count/sum/aggr values if applicable
            counts = []
            for col in df.columns:
                if any(x in col.lower() for x in ["count", "total", "sum", "repairs", "fault_count"]):
                    counts.extend(df[col].dropna().tolist())
                    
            provenance_info = ""
            if record_identifiers:
                provenance_info += f"Record Identifiers: {', '.join(record_identifiers[:15])}\n"
            if counts:
                provenance_info += f"Aggregation Counts/Sums: {', '.join(str(c) for c in counts[:15])}\n"
            else:
                provenance_info += f"Aggregation Counts/Sums: {len(df)} records\n"
                
            result_str = f"{markdown_table}\n\nMetadata Provenance:\n{provenance_info.strip()}"
            
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
