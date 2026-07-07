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
from src.services.site_map import resolve_site_mentions, SITE_MAP
from src.services.account_map import resolve_account_mentions

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


def _enrich_with_ppi(
    emr_names: list[str],
    query: str = "",
    max_direct: int = 5,
    max_fallback: int = 3,
    ppi_ids: list[str] = None
) -> tuple[str, list[dict]]:
    """
    Returns (display_str, ppi_list).
    - display_str  : formatted text appended to LLM context
    - ppi_list     : structured list[dict] with keys
                     {external_id, improvement_name, salesforce_url}
                     consumed by inject_ppi_links() in the Streamlit frontend
    """
    gc = get_graph_client()
    lines = []
    ppi_list: list[dict] = []
    ppi_ids = ppi_ids or []
    
    # Check if user explicitly asked for PPI. If not, do NOT return PPI.
    import re
    if query and not re.search(r'\bppi\b|\btechcare\b|\bproduct problem information\b', query, re.IGNORECASE):
        return "", []
    
    # Extract EMR IDs mentioned in the query (e.g., U-00013147) as direct targets
    if query:
        query_emrs = re.findall(r"\bU-\d{8}\b", query, re.IGNORECASE)
        if query_emrs:
            emr_names = list(set(emr_names + [name.upper() for name in query_emrs]))
            
    try:
        emr_ppis = gc.get_ppi_for_emrs(emr_names)
        if emr_ppis:
            lines.append("\n\n--- PPI (Product Problem Information) ---")
            shown = 0
            for emr_name, ppis in emr_ppis.items():
                if shown >= max_direct:
                    break
                for ppi in ppis[:2]:
                    ext_id = ppi['external_id']
                    name   = ppi['improvement_name']
                    sf_url = ppi.get('salesforce_url') or ""
                    lines.append(f"- {emr_name} \u2192 {ext_id}: {name}")
                    ppi_list.append({
                        "external_id":      ext_id,
                        "improvement_name": name,
                        "salesforce_url":   sf_url,
                        "emr_name":         emr_name,
                    })
                    shown += 1

        direct_ppis = gc.get_ppi_details_by_ids(ppi_ids)
        if direct_ppis:
            if not emr_ppis:
                lines.append("\n\n--- PPI (Product Problem Information) ---")
            for ppi in direct_ppis:
                ext_id = ppi['external_id']
                name   = ppi['improvement_name']
                sf_url = ppi.get('salesforce_url') or ""
                # Avoid duplicating lines if already added via EMR
                if not any(p["external_id"] == ext_id for p in ppi_list):
                    lines.append(f"- {ext_id}: {name}")
                    ppi_list.append({
                        "external_id":      ext_id,
                        "improvement_name": name,
                        "salesforce_url":   sf_url,
                        "emr_name":         None,
                    })

        if not emr_ppis and not direct_ppis and query:
            embedder = get_embeddings()
            fallback = gc.find_ppi_by_symptom_component(query, embedder, limit=max_fallback, score_threshold=0.5)
            if fallback:
                lines.append("\n\n--- PPI (semantik) ---")
                lines.append("Tidak ada PPI langsung. Berdasarkan kesamaan semantik, berikut PPI yang relevan:")
                for ppi in fallback:
                    score  = ppi.get("score", 0)
                    ext_id = ppi['external_id']
                    name   = ppi['improvement_name']
                    sf_url = ppi.get('salesforce_url') or ""
                    lines.append(f"- {ext_id}: {name} (kesamaan: {score:.2f})")
                    ppi_list.append({
                        "external_id":      ext_id,
                        "improvement_name": name,
                        "salesforce_url":   sf_url,
                        "emr_name":         None,
                    })
    except Exception as e:
        logger.debug(f"PPI enrichment skipped: {e}")
    # Deduplicate by external_id — multiple EMRs can share the same PPI
    seen: set[str] = set()
    deduped: list[dict] = []
    for entry in ppi_list:
        eid = entry["external_id"]
        if eid not in seen:
            seen.add(eid)
            deduped.append(entry)
    return "\n".join(lines), deduped


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

            # Extract EMR names from graph traversal context so that the
            # direct HAS_PPI Neo4j lookup works. We check both the seed entity
            # and any neighbor nodes that start with 'U-'.
            graph_raw_rows = (result.graph_context or {}).get("raw_rows", [])
            emr_names_from_graph = set()
            for row in graph_raw_rows:
                ent = str(row.get("entity", ""))
                neigh = str(row.get("neighbor", ""))
                if ent.startswith("U-"):
                    emr_names_from_graph.add(ent)
                if neigh.startswith("U-"):
                    emr_names_from_graph.add(neigh)

            ppi_info, ppi_list = _enrich_with_ppi(list(emr_names_from_graph), query=query)
            if ppi_info:
                answer += ppi_info

            return {
                "answer": answer,
                "chunks": [],
                "sql": None,
                "graph_traversal": result.graph_context,
                "ppi_links": ppi_list or None,
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





def _inject_sql_condition(sql: str, cond: str) -> str:
    if not cond:
        return sql
    cond = f"({cond})"

    sql_strip = sql.strip()
    has_semicolon = sql_strip.endswith(";")
    if has_semicolon:
        sql_strip = sql_strip[:-1].rstrip()

    # Removed hardcoded community_id check to make it generic

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
    sql = re.sub(
        r"\(\s*'[\w-]+'\s*=\s*ANY\(\s*community_id\s*\)(?:\s+OR\s+'[\w-]+'\s*=\s*ANY\(\s*community_id\s*\))*\s*\)",
        "",
        sql,
        flags=re.IGNORECASE,
    ).strip()
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

            site_query, site_hint = resolve_site_mentions(query)
            if site_hint:
                logger.info(f"Site resolution: {site_hint}")
            span.set_attribute("has_site_hint", site_hint is not None)

            account_query, account_hint = resolve_account_mentions(query)
            if account_hint:
                logger.info(f"Account resolution: {account_hint}")
            span.set_attribute("has_account_hint", account_hint is not None)

            symptom_cids = community_info.get("symptom_community_ids", [])
            has_model_entities = (
                resolved.entities
                and any(e.entity_type == "model" for e in resolved.entities)
            )
            should_inject_community = bool(symptom_cids) and not has_model_entities
            span.set_attribute("inject_community_id", should_inject_community)
            if resolved.entities and has_model_entities:
                logger.info("Model entities present — skipping community_id injection")

            modified = resolved.modified_query
            
            if site_hint or account_hint:
                filters = []
                if site_hint:
                    filters.append(site_hint)
                if account_hint:
                    filters.append(account_hint)
                combined_hint = " AND ".join(filters)
                if modified != query:
                    modified = f"{modified}. WAJIB gunakan filter pasti ini untuk lokasi/customer, JANGAN GUNAKAN ILIKE UNTUK LOKASI: {combined_hint}."
                else:
                    modified = f"{query}. WAJIB gunakan filter pasti ini untuk lokasi/customer, JANGAN GUNAKAN ILIKE UNTUK LOKASI: {combined_hint}."
            if should_inject_community:
                cid_hint = " OR ".join(f"'{c}' = ANY(community_id)" for c in symptom_cids)
                modified = f"{modified} WAJIB Gunakan HANYA filter community_id ini untuk masalah: {cid_hint}. JANGAN gunakan filter ILIKE untuk symptom/problem!"
            else:
                has_only_models = (
                    resolved.entities
                    and all(e.entity_type == "model" for e in resolved.entities)
                )
                if has_only_models:
                    modified = f"{modified} JANGAN gunakan community_id — query ini murni filter model."
                else:
                    modified = f"{modified} JANGAN gunakan community_id."
                    
            if "tunjukkan emr" in query.lower() or "tampilkan emr" in query.lower() or "list emr" in query.lower():
                modified = f"{modified} User meminta list EMR. WAJIB HANYA SELECT emr_name, ppi_external_id, ppi_improvement_name. JANGAN GUNAKAN FUNGSI AGREGASI COUNT() SAMA SEKALI!"
            if "ppi" in query.lower():
                modified = f"{modified} JANGAN PERNAH menambahkan filter 'ppi_external_id IS NOT NULL' pada klausa WHERE! Hitung semua masalah secara normal terlepas apakah memiliki PPI atau tidak."

            vn = get_vanna()
            sql = vn.generate_sql(modified, allow_llm_to_see_data=False)

            if not sql:
                span.set_attribute("sql_generated", False)
                return {"answer": "Could not generate SQL for the query.", "chunks": None, "sql": None, "sql_data": None}

            span.set_attribute("sql_generated", True)
            span.set_attribute("sql_query", sql)

            if should_inject_community:
                if not re.search(r'\bcommunity_id\b', sql, re.IGNORECASE):
                    sql = _inject_sql_condition(sql, cid_hint)
            else:
                sql = _strip_community_filter(sql)
                
            if site_hint or account_hint:
                combined_hint = " AND ".join(filter(None, [site_hint, account_hint]))
                sql = _inject_sql_condition(sql, combined_hint)

            if not _is_safe_select_query(sql):
                logger.warning(f"Blocked unsafe or non-SELECT query generated by LLM: {sql}")
                return {"answer": "Blocked unsafe or non-SELECT query generated by LLM.", "chunks": None, "sql": sql, "sql_data": None}

            logger.info(f"Executing sandboxed SQL: {sql}")

            df = vn.run_sql(sql)

            if df is None or df.empty:
                result_str = "No data returned from database."
                sql_data = []
                record_identifiers = []
                span.set_attribute("rows_returned", 0)
            else:
                span.set_attribute("rows_returned", len(df))
                span.set_attribute("columns_returned", len(df.columns))
                
                # Prioritize EMRs with PPIs in the summarized dataframe
                if "ppi_external_id" in df.columns:
                    df = df.sort_values(by="ppi_external_id", na_position="last").reset_index(drop=True)
                
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

            ppi_ids = []
            if df is not None and not df.empty and "ppi_external_id" in df.columns:
                ppi_ids = df["ppi_external_id"].dropna().unique().astype(str).tolist()

            ppi_info, ppi_list = _enrich_with_ppi(record_identifiers, ppi_ids=ppi_ids, query=query)
            if ppi_info:
                result_str += ppi_info

            return {"answer": result_str, "chunks": None, "sql": sql, "sql_data": sql_data, "resolved_entities": resolved_info, "ppi_links": ppi_list or None}
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
            from src.services.site_map import resolve_site_mentions
            from src.services.account_map import resolve_account_mentions
            
            site_query, site_hint = resolve_site_mentions(query)
            if site_hint:
                logger.info(f"Site resolution (Graph): {site_hint}")
            span.set_attribute("has_site_hint", site_hint is not None)

            account_query, account_hint = resolve_account_mentions(query)
            if account_hint:
                logger.info(f"Account resolution (Graph): {account_hint}")
            span.set_attribute("has_account_hint", account_hint is not None)

            resolver = EntityResolver(
                get_graph_client(),
                get_llm(temperature=0.0),
                get_embeddings(),
            )
            result = resolver.search_emr_records(query, site_hint=site_hint, account_hint=account_hint)
            canonical_names = result.get("canonical_names", [])
            neo4j_records = result.get("emr_records", [])
            entities = result.get("entities", [])

            if not neo4j_records:
                return {"answer": "Tidak ditemukan EMR records yang cocok dengan deskripsi tersebut.", "emr_records": [], "entities": entities}

            emr_names = [r["emr_name"] for r in neo4j_records if r.get("emr_name")]

            _, account_hint = resolve_account_mentions(query)
            if account_hint:
                logger.info(f"Account filter for EMR records: {account_hint}")
            account_cond = f" AND ({account_hint})" if account_hint else ""

            full_records = []
            if emr_names:
                try:
                    from sqlalchemy import create_engine, text
                    from src.config import settings
                    engine = create_engine(settings.readonly_postgres_url)
                    with engine.connect() as conn:
                        placeholders = ", ".join(f":n{i}" for i in range(len(emr_names)))
                        params = {f"n{i}": name for i, name in enumerate(emr_names)}
                        sql = f"SELECT * FROM emr_records WHERE emr_name IN ({placeholders}){account_cond} ORDER BY emr_name"
                        rows = conn.execute(
                            text(sql),
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

            full_answer = "\n".join(answer_lines)
            ppi_info, ppi_list = _enrich_with_ppi(emr_names, query=query)
            if ppi_info:
                full_answer += ppi_info

            return {"answer": full_answer, "emr_records": full_records or neo4j_records, "entities": entities, "ppi_links": ppi_list or None}
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


class SmrAnalysisArgs(BaseModel):
    query: str = Field(description="The user's query about SMR/service meter readings for a specific problem, e.g., 'hydraulic leak muncul di smr berapa saja'")


@register_tool(args_schema=SmrAnalysisArgs)
def analyze_smr(query: str) -> Dict[str, Any]:
    from src.services.telemetry import tracer
    with tracer.start_as_current_span("analyze_smr") as span:
        span.set_attribute("tool_query", query)
        logger.info(f"Using tool analyze_smr for query: {query}")
        try:
            resolver = EntityResolver(
                get_graph_client(),
                get_llm(temperature=0.0),
                get_embeddings(),
            )
            resolved = resolver.resolve_query(query)
            community_info = resolver.resolve_mentions_to_community_ids(query)

            symptom_cids = community_info.get("symptom_community_ids", [])
            canonical_names = community_info.get("canonical_names", [])

            site_query, site_hint = resolve_site_mentions(query)
            if site_hint:
                logger.info(f"SMR site resolution: {site_hint}")

            account_query, account_hint = resolve_account_mentions(query)
            if account_hint:
                logger.info(f"SMR account resolution: {account_hint}")

            from sqlalchemy import create_engine, text
            from src.config import settings

            engine = create_engine(settings.readonly_postgres_url)
            with engine.connect() as conn:
                site_cond = f" AND ({site_hint})" if site_hint else ""
                account_cond = f" AND ({account_hint})" if account_hint else ""
                
                model_entities = [e.canonical_name for e in resolved.entities if e.entity_type == "model"]
                model_conds = []
                if model_entities:
                    brand_codes = list(EntityResolver._BRAND_MAP.values())
                    or_conds_list = []
                    for m in model_entities:
                        if m in brand_codes:
                            or_conds_list.append(f"machine_product = '{m}'")
                        else:
                            or_conds_list.append(f"(machine_model ILIKE '%{m}%' OR model_family ILIKE '%{m}%')")
                    if or_conds_list:
                        model_conds.append(f"({' OR '.join(or_conds_list)})")
                model_cond = f" AND {' AND '.join(model_conds)}" if model_conds else ""
                
                extra_cond = f"{site_cond}{account_cond}{model_cond}"
                if symptom_cids:
                    cond = " OR ".join(f"'{c}' = ANY(community_id)" for c in symptom_cids)
                    sql = f"""
                        SELECT smr_trouble, emr_name, created_date, symptom, machine_model
                        FROM emr_records
                        WHERE ({cond}){extra_cond}
                        ORDER BY created_date
                    """
                elif canonical_names:
                    ilike_names = list(set(canonical_names + community_info.get("expanded_names", [])))
                    ors = " OR ".join(
                        f"(symptom ILIKE '%{n}%' OR caused_of_problem ILIKE '%{n}%' OR subjects ILIKE '%{n}%')"
                        for n in ilike_names
                    )
                    sql = f"""
                        SELECT smr_trouble, emr_name, created_date, symptom, machine_model
                        FROM emr_records
                        WHERE {ors}{extra_cond}
                        ORDER BY created_date
                    """
                else:
                    return {"answer": "Tidak dapat mengidentifikasi masalah dari query.", "smr_data": [], "count": 0}

                if not _is_safe_select_query(sql):
                    logger.warning(f"Blocked unsafe SQL in analyze_smr: {sql}")
                    return {"answer": "Blocked unsafe query.", "smr_data": [], "count": 0}

                logger.info(f"Executing SMR analysis SQL: {sql}")
                rows = conn.execute(text(sql)).fetchall()
                cols = rows[0]._fields if rows else []

            smr_data = []
            for row in rows:
                d = dict(zip(cols, row))
                smr_val = d.get("smr_trouble")
                if smr_val is not None:
                    smr_data.append({
                        "smr": float(smr_val) if not isinstance(smr_val, float) else smr_val,
                        "emr_name": d.get("emr_name", ""),
                        "created_date": str(d.get("created_date", ""))[:10] if d.get("created_date") else "",
                        "symptom": d.get("symptom", "")[:80] if d.get("symptom") else "",
                        "machine_model": d.get("machine_model", ""),
                    })

            answer = (
                f"Ditemukan {len(smr_data)} record dengan SMR untuk masalah terkait.\n"
                f"Rentang SMR: {min(d['smr'] for d in smr_data):.0f} - {max(d['smr'] for d in smr_data):.0f}\n"
                f"Rata-rata SMR: {sum(d['smr'] for d in smr_data) / len(smr_data):.0f}\n\n"
                f"Data divisualisasikan di bagian grafik."
            ) if smr_data else "Tidak ada data SMR yang ditemukan untuk masalah tersebut."

            ppi_info, ppi_list = _enrich_with_ppi([d["emr_name"] for d in smr_data], query=query)
            if ppi_info:
                answer += ppi_info

            return {
                "answer": answer,
                "smr_data": smr_data,
                "count": len(smr_data),
                "ppi_links": ppi_list or None,
                "entities": [
                    {"mention": e.mention, "canonical_name": e.canonical_name,
                     "type": e.entity_type, "score": round(e.score, 3)}
                    for e in resolved.entities
                ] if resolved.entities else [],
            }

        except Exception as e:
            span.record_exception(e)
            logger.error(f"Error in analyze_smr: {e}")
            return {"answer": f"Error analyzing SMR data: {e}", "smr_data": [], "count": 0}
