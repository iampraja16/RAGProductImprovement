"""Agent tools (Refactored to use new retrieval architecture)."""

import logging
from typing import Dict, Any, List, Type, Optional
from pydantic import BaseModel, Field
import inspect

from src.services.providers import get_vanna, get_graph_client, get_embeddings, get_llm
from src.services.entity_resolver import EntityResolver
from src.graph.retrieval.local import LocalSearchRetriever
from src.graph.retrieval.global_search import GlobalSearchRetriever
from src.graph.retrieval.drift import DriftSearchRetriever
from src.agent.prompts import estimate_tokens, truncate_to_tokens

import pandas as pd

logger = logging.getLogger(__name__)


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


class QueryArgs(BaseModel):
    query: str = Field(description="The user's query or question.")

class ReportArgs(BaseModel):
    family: str = Field(description="The model family to generate the report for, e.g., 'PC200', 'HD465'.")

class GraphRetrievalArgs(BaseModel):
    query: str = Field(description="The user's query or question.")
    mode: str = Field(default="drift", description="Retrieval mode: 'local', 'global', or 'drift'")


@register_tool(args_schema=GraphRetrievalArgs)
def ask_emr_graph(query: str, mode: str = "drift") -> Dict[str, Any]:
    from src.services.telemetry import tracer
    with tracer.start_as_current_span("ask_emr_graph") as span:
        span.set_attribute("tool_query", query)
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
            span.record_exception(e)
            logger.error(f"Error in ask_emr_graph: {e}")
            return {"answer": f"Error querying knowledge graph: {e}", "chunks": [], "sql": None, "graph_traversal": None}

import re
from src.config import settings

def _is_safe_select_query(sql: str) -> bool:
    sql_clean = re.sub(r'--.*$', '', sql, flags=re.MULTILINE)
    sql_clean = re.sub(r'/\*.*?\*/', '', sql_clean, flags=re.DOTALL)
    sql_clean = sql_clean.strip()
    sql_no_strings = re.sub(r"'[^']*(?:''[^']*)*'", "''", sql_clean)
    pattern = r'^(?:\s*\(?)*\s*(?:SELECT|WITH)\b'
    if not re.match(pattern, sql_no_strings, re.IGNORECASE):
        return False
    forbidden_keywords = [
        r'\bINSERT\b', r'\bUPDATE\b', r'\bDELETE\b', r'\bDROP\b', r'\bALTER\b',
        r'\bCREATE\b', r'\bTRUNCATE\b', r'\bREPLACE\b', r'\bGRANT\b', r'\bREVOKE\b',
        r'\bCOPY\b', r'\bMERGE\b', r'\bCALL\b', r'\bEXECUTE\b', r'\bDO\b',
        r'\bVACUUM\b', r'\bANALYZE\b'
    ]
    for kw_pattern in forbidden_keywords:
        if re.search(kw_pattern, sql_no_strings, re.IGNORECASE):
            return False
    sql_check = sql_no_strings.strip()
    if sql_check.endswith(';'):
        sql_check = sql_check[:-1].rstrip()
    if ';' in sql_check:
        return False
    return True

def _inject_limit_if_missing(sql: str, default_limit: int) -> str:
    sql_strip = sql.strip()
    has_semicolon = sql_strip.endswith(';')
    if has_semicolon:
        sql_strip = sql_strip[:-1].rstrip()
    sql_no_strings = re.sub(r"'[^']*(?:''[^']*)*'", "''", sql_strip)
    if not re.search(r'\bLIMIT\b', sql_no_strings, re.IGNORECASE):
        sql_strip = f"{sql_strip} LIMIT {default_limit}"
    if has_semicolon:
        sql_strip = f"{sql_strip};"
    return sql_strip

def _summarize_dataframe(df: pd.DataFrame) -> str:
    if df.empty:
        return "No data returned."
    if len(df) <= settings.dataframe_markdown_limit:
        if len(df.columns) > 15:
            df = df.iloc[:, :15]
        return df.to_markdown(index=False)
    total_rows = len(df)
    total_cols = len(df.columns)
    sample_df = df
    if total_cols > 15:
        sample_df = df.iloc[:, :15]
    summary_parts = []
    summary_parts.append(f"**Data Summary**: {total_rows} rows x {total_cols} columns")
    summary_parts.append("\n**Top 10 Sample Rows:**")
    summary_parts.append(sample_df.head(10).to_markdown(index=False))
    numeric_cols = df.select_dtypes(include=['number']).columns
    if not numeric_cols.empty:
        stats = []
        for col in numeric_cols[:10]:
            try:
                col_min = df[col].min()
                col_max = df[col].max()
                col_mean = df[col].mean()
                stats.append(f"- {col}: Min={col_min}, Max={col_max}, Mean={col_mean:.2f}")
            except Exception:
                pass
        if stats:
            summary_parts.append("\n**Numeric Statistics:**")
            summary_parts.extend(stats)
    cat_cols = df.select_dtypes(exclude=['number']).columns
    if not cat_cols.empty:
        cat_stats = []
        for col in cat_cols[:10]:
            try:
                top_vals = df[col].value_counts().head(3).to_dict()
                cat_stats.append(f"- {col}: Top Values={top_vals}")
            except Exception:
                pass
        if cat_stats:
            summary_parts.append("\n**Categorical Statistics:**")
            summary_parts.extend(cat_stats)
    full_summary = "\n".join(summary_parts)
    return truncate_to_tokens(full_summary, settings.dataframe_summary_token_limit)


def _build_iliake_count_query(query: str, canonical_names: List[str]) -> Optional[str]:
    stopwords = {
        "yang", "di", "ke", "dan", "atau", "pada", "dengan", "untuk",
        "saya", "kamu", "ini", "itu", "ada", "tidak", "akan", "dapat",
        "the", "a", "an", "of", "in", "on", "to", "for", "with",
        "and", "or", "is", "are", "was", "were",
        "please", "show", "list", "find", "cari",
        "tampilkan", "sebutkan", "berikan", "tolong", "mohon",
        "membahas", "mengenai", "tentang", "dimana", "bagaimana",
        "apakah", "adakah", "berapa", "total", "banyak",
        "semua", "setiap", "beberapa", "kerusakan", "masalah",
    }
    words = set()
    for name in canonical_names:
        for w in re.findall(r"[a-zA-Z0-9]+", name):
            wl = w.lower()
            if len(wl) > 2 and wl not in stopwords:
                words.add(wl)
    for w in re.findall(r"[a-zA-Z0-9]+", query):
        wl = w.lower()
        if len(wl) > 2 and wl not in stopwords:
            words.add(wl)
    if not words:
        return None
    # Separate model-looking keywords from text keywords
    text_cols = ["symptom", "caused_of_problem", "action_how_was_problem_corrected", "subjects"]
    model_kws = []
    text_kws = []
    for kw in sorted(words)[:6]:
        # If kw looks like a model code (starts with letter, contains digits), search machine_model
        if re.search(r'^[a-z]+\d', kw) and any(c.isdigit() for c in kw):
            model_kws.append(kw)
        else:
            text_kws.append(kw)
    col_pats = []
    for kw in model_kws:
        col_pats.append(f"(machine_model ILIKE '%{kw}%' OR model_family ILIKE '%{kw}%')")
    for kw in text_kws:
        ors = " OR ".join(f"{c} ILIKE '%{kw}%'" for c in text_cols)
        col_pats.append(f"({ors})")
    if not col_pats:
        return None
    conds = " AND ".join(col_pats)
    return f"SELECT COUNT(*) FROM emr_records WHERE {conds} LIMIT 100;"


def _inject_community_filter(sql: str, community_ids: List[str]) -> str:
    if not community_ids:
        return sql
    cond = " OR ".join(f"'{c}' = ANY(community_id)" for c in community_ids)
    cond = f"({cond})"

    sql_strip = sql.strip()
    has_semicolon = sql_strip.endswith(";")
    if has_semicolon:
        sql_strip = sql_strip[:-1].rstrip()

    if re.search(r'\bcommunity_id\b', sql_strip, re.IGNORECASE):
        return sql

    where_match = re.search(r'\bWHERE\b', sql_strip, re.IGNORECASE)
    group_match = re.search(r'\bGROUP\s+BY\b', sql_strip, re.IGNORECASE)
    order_match = re.search(r'\bORDER\s+BY\b', sql_strip, re.IGNORECASE)
    limit_match = re.search(r'\bLIMIT\b', sql_strip, re.IGNORECASE)

    # Find the FIRST clause after WHERE (GROUP BY, ORDER BY, or LIMIT)
    after_where = where_match.end() if where_match else 0
    candidates = [m.start() for m in [group_match, order_match, limit_match] if m and m.start() >= after_where]
    insert_pos = min(candidates) if candidates else len(sql_strip)

    if where_match:
        clause = f" AND {cond} "
        result = sql_strip[:insert_pos] + clause + sql_strip[insert_pos:]
    else:
        clause = f" WHERE {cond} "
        result = sql_strip[:insert_pos] + clause + sql_strip[insert_pos:]

    if has_semicolon:
        result = result.rstrip() + ";"
    return result


def _strip_community_filter(sql: str) -> str:
    """Remove any community_id filter from SQL (for model/brand-only queries)."""
    # Remove full parenthesized community_id condition: ('x' = ANY(...) OR 'y' = ANY(...))
    sql = re.sub(
        r"\(\s*'[\w-]+'\s*=\s*ANY\(\s*community_id\s*\)(?:\s+OR\s+'[\w-]+'\s*=\s*ANY\(\s*community_id\s*\))*\s*\)",
        "",
        sql,
        flags=re.IGNORECASE,
    ).strip()
    # Remove single community_id filter: AND 'x' = ANY(...) or WHERE 'x' = ANY(...)
    sql = re.sub(
        r"(AND\s+)?'[\w-]+'\s*=\s*ANY\(\s*community_id\s*\)",
        "",
        sql,
        flags=re.IGNORECASE,
    ).strip()
    # Clean up dangling: WHERE AND → WHERE, WHERE OR → WHERE, trailing AND/OR
    sql = re.sub(r"\s+WHERE\s+(AND|OR)\s+", " WHERE ", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\s+(AND|OR)\s+WHERE\s+", " WHERE ", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\s+(AND|OR)\s+(GROUP\s+BY|ORDER\s+BY|LIMIT|;|$)", r" \2", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\s+WHERE\s+(GROUP\s+BY|ORDER\s+BY|LIMIT|;|$)", r" \1", sql, flags=re.IGNORECASE)
    if sql.endswith("WHERE "):
        sql = sql[:-6].strip()
    if sql.endswith("WHERE"):
        sql = sql[:-5].strip()
    return sql


@register_tool(args_schema=QueryArgs)
def ask_emr_database(query: str) -> Dict[str, Any]:
    from src.services.telemetry import tracer
    with tracer.start_as_current_span("ask_emr_database") as span:
        span.set_attribute("tool_query", query)
        logger.info(f"Using tool ask_emr_database for query: {query}")
        try:
            from src.services.providers import get_vanna
            resolver = EntityResolver(
                get_graph_client(),
                get_llm(temperature=0.0),
                get_embeddings(),
            )
            resolved = resolver.resolve_query(query)
            community_info = resolver.resolve_mentions_to_community_ids(query)

            if resolved.entities:
                logger.info(f"Entity resolution: {[(e.mention, e.canonical_name, e.score) for e in resolved.entities]}")
            if community_info["community_ids"]:
                logger.info(f"Community IDs: {community_info['community_ids']}")

            # Only inject community_ids from symptom-type entities, not from model-type
            # (brands like "Komatsu" get falsely resolved to symptom nodes via fulltext)
            # Also skip if model entities are present — model filter + ILIKE is precise enough
            symptom_cids = community_info.get("symptom_community_ids", [])
            has_model_entities = (
                resolved.entities
                and any(e.entity_type == "model" for e in resolved.entities)
            )
            should_inject_community = bool(symptom_cids) and not has_model_entities
            span.set_attribute("inject_community_id", should_inject_community)
            if resolved.entities and has_model_entities:
                logger.info("Model entities present — skipping community_id injection")

            # Build query hint for Vanna
            modified = resolved.modified_query
            if should_inject_community:
                cid_hint = " OR ".join(f"'{c}' = ANY(community_id)" for c in symptom_cids)
                if modified != query:
                    modified = f"{modified}. Gunakan filter community_id: {cid_hint}"
                else:
                    modified = f"{query}. Gunakan filter community_id: {cid_hint}"
            else:
                if modified == query:
                    has_only_models = (
                        resolved.entities
                        and all(e.entity_type == "model" for e in resolved.entities)
                    )
                    if has_only_models:
                        modified = f"{query}. JANGAN gunakan community_id — query ini murni filter model."
                    else:
                        modified = f"{query}. JANGAN gunakan community_id."

            vn = get_vanna()
            sql = vn.generate_sql(modified, allow_llm_to_see_data=False)

            if not sql:
                span.set_attribute("sql_generated", False)
                return {"answer": "Could not generate SQL for the query.", "chunks": None, "sql": None, "sql_data": None}

            span.set_attribute("sql_generated", True)
            span.set_attribute("sql_query", sql)

            # Post-inject symptom community_id filter for reliability
            if should_inject_community:
                sql = _inject_community_filter(sql, symptom_cids)
            else:
                sql = _strip_community_filter(sql)

            if not _is_safe_select_query(sql):
                logger.warning(f"Blocked unsafe or non-SELECT query generated by LLM: {sql}")
                return {"answer": "Blocked unsafe or non-SELECT query generated by LLM.", "chunks": None, "sql": sql, "sql_data": None}

            sql = _inject_limit_if_missing(sql, settings.sql_row_limit)
            logger.info(f"Executing sandboxed SQL: {sql}")

            df = vn.run_sql(sql)

            # Also trigger fallback if COUNT query returned 0
            is_count_query_zero = (
                df is not None
                and not df.empty
                and len(df.columns) == 1
                and any(x in str(df.columns[0]).lower() for x in ["count", "total"])
                and df.iloc[0, 0] == 0
            )
            if (df is None or df.empty or is_count_query_zero) and should_inject_community:
                logger.info("Community ID search returned 0, falling back to ILIKE")
                fallback_sql = _build_iliake_count_query(query, community_info["canonical_names"])
                if fallback_sql:
                    logger.info(f"ILIKE fallback SQL: {fallback_sql}")
                    df = vn.run_sql(fallback_sql)
                    sql = fallback_sql

            if df is None or df.empty:
                result_str = "No data returned from database."
                sql_data = []
                span.set_attribute("rows_returned", 0)
            else:
                span.set_attribute("rows_returned", len(df))
                span.set_attribute("columns_returned", len(df.columns))
                markdown_table = _summarize_dataframe(df)
                df_cleaned = df.replace({float('nan'): None, float('inf'): None, float('-inf'): None})
                sql_data = df_cleaned.to_dict(orient="records")
                record_identifiers = []
                for col in ["emr_name", "model_name", "symptom", "name", "component", "model"]:
                    if col in df.columns:
                        record_identifiers.extend(df[col].dropna().unique().astype(str).tolist())
                counts = []
                for col in df.columns:
                    if any(x in col.lower() for x in ["count", "total", "sum", "repairs", "fault_count"]):
                        counts.extend(df[col].dropna().tolist())
                provenance_info = ""
                if record_identifiers:
                    provenance_info += f"Record Identifiers: {', '.join(record_identifiers[:15])}\n"

                # If this is a listing query with LIMIT, try to get actual total count
                limit_match = re.search(r'LIMIT\s+(\d+)', sql, re.IGNORECASE)
                if limit_match and len(df) > 0 and not counts:
                    limit_val = int(limit_match.group(1))
                    if len(df) >= limit_val:
                        try:
                            count_sql = re.sub(
                                r'\bSELECT\b.*?\bFROM\b',
                                'SELECT COUNT(*) AS actual_total FROM',
                                sql,
                                count=1, flags=re.IGNORECASE
                            )
                            count_sql = re.sub(r'\bLIMIT\s+\d+\b', '', count_sql, flags=re.IGNORECASE)
                            count_sql = re.sub(r'\bORDER\s+BY\s+.+?(?=LIMIT|GROUP|$)', '', count_sql, flags=re.IGNORECASE)
                            count_sql = count_sql.strip().rstrip(';')
                            count_df = vn.run_sql(count_sql)
                            if count_df is not None and not count_df.empty:
                                actual = count_df.iloc[0, 0]
                                provenance_info += f"Actual total records matching filter: {actual} (showing top {len(df)})\n"
                                span.set_attribute("actual_total", int(actual))
                        except Exception as e:
                            logger.debug(f"Count query failed: {e}")
                            provenance_info += f"Showing {len(df)} records\n"
                    else:
                        provenance_info += f"Total: {len(df)} records\n"
                elif counts:
                    provenance_info += f"Aggregation Counts/Sums: {', '.join(str(c) for c in counts[:15])}\n"
                else:
                    provenance_info += f"Aggregation Counts/Sums: {len(df)} records\n"
                result_str = f"{markdown_table}\n\nMetadata Provenance:\n{provenance_info.strip()}"

            resolved_info = None
            if resolved and resolved.entities:
                resolved_info = [
                    {"mention": e.mention, "canonical_name": e.canonical_name,
                     "type": e.entity_type, "score": round(e.score, 3)}
                    for e in resolved.entities
                ]
            return {"answer": result_str, "chunks": None, "sql": sql, "sql_data": sql_data, "resolved_entities": resolved_info}
        except Exception as e:
            span.record_exception(e)
            logger.error(f"Error in ask_emr_database: {e}")
            return {"answer": f"Error querying database: {e}", "chunks": None, "sql": None, "sql_data": None}


@register_tool(args_schema=QueryArgs)
def search_emr_records(query: str) -> Dict[str, Any]:
    from src.services.telemetry import tracer
    with tracer.start_as_current_span("search_emr_records") as span:
        span.set_attribute("tool_query", query)
        logger.info(f"Using tool search_emr_records for query: {query}")
        try:
            resolver = EntityResolver(
                get_graph_client(),
                get_llm(temperature=0.0),
                get_embeddings(),
            )
            result = resolver.search_emr_records(query)
            canonical_names = result.get("canonical_names", [])
            neo4j_records = result.get("emr_records", [])
            entities = result.get("entities", [])

            if not neo4j_records:
                return {"answer": "Tidak ditemukan EMR records yang cocok dengan deskripsi tersebut.", "emr_records": [], "entities": entities}

            emr_names = [r["emr_name"] for r in neo4j_records if r.get("emr_name")]

            # Enrich with full details from PostgreSQL
            full_records = []
            if emr_names:
                try:
                    from sqlalchemy import create_engine, text
                    from src.config import settings
                    engine = create_engine(settings.readonly_postgres_url)
                    with engine.connect() as conn:
                        placeholders = ", ".join(f":n{i}" for i in range(len(emr_names)))
                        params = {f"n{i}": name for i, name in enumerate(emr_names)}
                        rows = conn.execute(
                            text(f"SELECT * FROM emr_records WHERE emr_name IN ({placeholders}) ORDER BY emr_name"),
                            params
                        ).fetchall()
                        cols = rows[0]._fields if rows else []
                        full_records = [dict(zip(cols, row)) for row in rows]
                except Exception as e:
                    logger.warning(f"PostgreSQL enrichment failed: {e}")

            display = neo4j_records[:5]
            if full_records:
                answer_lines = [f"Menampilkan {len(display)} EMR:"]
                for i, rec in enumerate(full_records[:5], 1):
                    lines = [f"  {i}. {rec.get('emr_name', '')}"]
                    for col, val in rec.items():
                        if col in ("emr_name", "community_id", "graph_community_summary"):
                            continue
                        if val is not None and val != "":
                            label = col.replace("_", " ").title()
                            lines.append(f"     {label}: {val}")
                    answer_lines.append("\n".join(lines))
            else:
                answer_lines = [f"Menampilkan {len(display)} EMR:"]
                for i, rec in enumerate(display, 1):
                    symptom = rec.get("symptom", "") or ""
                    model = rec.get("model_family", "") or ""
                    machine = rec.get("machine_model", "") or ""
                    rc = rec.get("root_causes", []) or []
                    act = rec.get("actions", []) or []
                    desc = f"{symptom[:60]}" if symptom else "(no symptom)"
                    if model:
                        desc += f" | {model}"
                    if machine:
                        desc += f" | {machine}"
                    if rc:
                        desc += f" | RootCause: {rc[0][:40]}"
                    if act:
                        desc += f" | Action: {act[0][:40]}"
                    answer_lines.append(f"  {i}. {rec['emr_name']} — {desc}")

            if canonical_names:
                answer_lines.append(f"\nPencarian berdasarkan: {', '.join(canonical_names)}")

            return {"answer": "\n".join(answer_lines), "emr_records": full_records or neo4j_records, "entities": entities}
        except Exception as e:
            span.record_exception(e)
            logger.error(f"Error in search_emr_records: {e}")
            return {"answer": f"Error searching EMR records: {e}", "emr_records": [], "entities": []}


@register_tool(args_schema=ReportArgs)
def generate_executive_summary(family: str) -> Dict[str, Any]:
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
