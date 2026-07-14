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

_entity_resolver_instance = None

def _get_entity_resolver():
    global _entity_resolver_instance
    if _entity_resolver_instance is None:
        _entity_resolver_instance = EntityResolver(
            get_graph_client(), get_llm(temperature=0.0), get_embeddings()
        )
    return _entity_resolver_instance


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


def _build_full_context(query: str) -> dict:
    resolver = _get_entity_resolver()
    return resolver.resolve_full_context(query)


@register_tool(args_schema=GraphRetrievalArgs)
def ask_emr_graph(query: str, mode: str = "drift", resolved_context: Optional[dict] = None) -> Dict[str, Any]:
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
    sql = re.sub(
        r"\bAND\s+\S*\.?community_id\s+IN\s*\([^)]*\)",
        "",
        sql,
        flags=re.IGNORECASE,
    ).strip()
    sql = re.sub(
        r"\bAND\s+\S*\.?community_id\s+IS\s+(NOT\s+)?NULL",
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
    # Warn if community_id still present in WHERE after stripping
    where_idx = re.search(r'\bWHERE\b', sql, re.IGNORECASE)
    if where_idx:
        after_where = sql[where_idx.start():]
        if re.search(r'\bcommunity_id\b', after_where, re.IGNORECASE):
            logger.warning(f"community_id still present in WHERE after stripping: {sql[:200]}")
    return sql


_STOP_WORDS_ILIKE = frozenset({
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "with",
    "and", "or", "is", "are", "was", "were", "this", "that", "these",
    "from", "by", "be", "been", "being", "have", "has", "had", "do",
    "does", "did", "but", "not", "no", "yes", "its", "it", "as",
})


def _expand_ilipe_patterns(sql: str) -> str:
    """Expand multi-word ILIKE patterns — phrase OR all-words-AND for precision+recall.

    E.g. WHERE symptom ILIKE '%hydraulic oil leak%'
    → WHERE (symptom ILIKE '%hydraulic oil leak%' OR (symptom ILIKE '%hydraulic%' AND symptom ILIKE '%oil%' AND symptom ILIKE '%leak%'))

    Uses AND for individual words (all must appear) instead of OR (any can appear),
    so 'oil leak FRONT SUSPENSION' won't match 'hydraulic oil leak'.
    """
    def _expand_one(m: re.Match) -> str:
        col = m.group(1)
        phrase = m.group(2)
        words = [w for w in phrase.split() if w.lower() not in _STOP_WORDS_ILIKE and len(w) >= 3]
        if len(words) <= 1:
            return m.group(0)
        and_conds = " AND ".join(f"{col} ILIKE '%{w}%'" for w in words)
        return f"({col} ILIKE '%{phrase}%' OR ({and_conds}))"

    return re.sub(
        r'(\w+(?:\.\w+)?)\s+ILIKE\s*\'%([^%\']+)%\'',
        _expand_one,
        sql,
        flags=re.IGNORECASE,
    )


_ADJUSTED_LIMIT = 50  # default limit for listing queries


def _adjust_small_limit(sql: str) -> str:
    """Increase LIMIT for non-aggregation listing queries so the user sees more rows.

    E.g. LIMIT 5 → LIMIT 50 for SELECT ... WHERE ... (without COUNT/GROUP BY).
    """
    if re.search(r'\bCOUNT\s*\(', sql, re.IGNORECASE):
        return sql
    m = re.search(r'\bLIMIT\s+(\d+)\b', sql, re.IGNORECASE)
    if m:
        current = int(m.group(1))
        if current <= 5:
            return re.sub(r'\bLIMIT\s+\d+\b', f'LIMIT {_ADJUSTED_LIMIT}', sql, flags=re.IGNORECASE)
    return sql


_QUESTION_WORDS = re.compile(
    r'\b(?:tentukan|berapa|bagaimana|apa|siapa|kapan|dimana|mengapa|'
    r'coba|tolong|saya|ingin|tahu|bisa|apakah|adakah|'
    r'mana|saja|seluruh|semua|yaitu|yakni|adalah|'
    r'untuk|dengan|dan|serta|atau|yang|di|ke|dari|pada|'
    r'hitung|beri|berikan|kasih|tunjukkan|tampilkan|buat|bantu|'
    r'show|tell|list|find|get|give|what|how|where|why|when|which|'
    r'please|help|want|need|can|does|is|are|was|were|do|did|has|have|had)\b',
    re.IGNORECASE
)


_DOMAIN_NOISE_WORDS = frozenset({
    "smr", "hm", "hour", "meter", "jam", "operasi", "distribution",
    "grafik", "chart", "plot", "scatter", "show", "tunjukkan",
    "list", "tampilkan", "cari", "find", "buatkan", "bantu",
    "tolong", "please", "help",
})


def _extract_symptom_keywords(query: str) -> list[str]:
    """Extract symptom-like bigrams from a raw query when entity resolution returns nothing.

    Strips question/stop/domain-noise words, then extracts multi-word sliding windows.
    Returns ONLY bigrams (not individual words) for better precision.
    """
    cleaned = _QUESTION_WORDS.sub("", query).strip()
    cleaned = re.sub(r'[^\w\s]', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    tokens = [t for t in cleaned.split()
              if len(t) >= 3
              and t.lower() not in _STOP_WORDS_ILIKE
              and t.lower() not in _DOMAIN_NOISE_WORDS]
    if not tokens:
        return []
    # Multi-word phrases only (sliding window of 2 tokens)
    phrases = list(dict.fromkeys(
        " ".join(tokens[i:i+2]) for i in range(len(tokens) - 1)
    ))
    return phrases[:10]  # limit to 10 bigrams


@register_tool(args_schema=QueryArgs)
def ask_emr_database(query: str, resolved_context: Optional[dict] = None) -> Dict[str, Any]:
    from src.services.telemetry import tracer
    with tracer.start_as_current_span("ask_emr_database") as span:
        span.set_attribute("tool_query", query)
        logger.info(f"Using tool ask_emr_database for query: {query}")
        try:
            ctx = resolved_context if resolved_context is not None else _build_full_context(query)
            entities = ctx.get("entities", [])
            modified = ctx.get("modified_query") or query
            site_hint = ctx.get("site_hint")
            account_hint = ctx.get("account_hint")
            should_inject_community = ctx.get("should_inject_community", False)
            cid_hint = ctx.get("cid_hint")
            has_model_entities = ctx.get("has_model_entities", False)

            if entities:
                logger.info(f"Entity resolution: {[(e['mention'], e['canonical_name'], e['score']) for e in entities]}")
            if ctx.get("symptom_community_ids"):
                logger.info(f"Community IDs: {ctx['symptom_community_ids']}")
            if site_hint:
                logger.info(f"Site resolution: {site_hint}")
            span.set_attribute("has_site_hint", site_hint is not None)
            if account_hint:
                logger.info(f"Account resolution: {account_hint}")
                _acct_names_in_hint = re.findall(r"'([^']+)'", account_hint)
                _valid_names = [n for n in _acct_names_in_hint if n.lower() in query.lower()]
                if not _valid_names:
                    logger.warning(f"Account hint '{account_hint}' has no match in original query — treating as false positive")
                    account_hint = None
            span.set_attribute("has_account_hint", account_hint is not None)
            span.set_attribute("inject_community_id", should_inject_community)

            if site_hint or account_hint:
                combined_hint = " AND ".join(f for f in [site_hint, account_hint] if f)
                modified = f"{modified}. WAJIB gunakan filter pasti ini untuk lokasi/customer, JANGAN GUNAKAN ILIKE UNTUK LOKASI: {combined_hint}."
            if should_inject_community and cid_hint:
                modified = f"{modified} WAJIB Gunakan HANYA filter community_id ini untuk masalah: {cid_hint}. JANGAN gunakan filter ILIKE untuk symptom/problem!"
            else:
                has_only_models = entities and all(e["entity_type"] == "model" for e in entities)
                if has_only_models:
                    modified = f"{modified} JANGAN gunakan community_id — query ini murni filter model."
                else:
                    modified = f"{modified} JANGAN gunakan community_id."

            graph_problem_names = ctx.get("graph_problem_names")
            if graph_problem_names:
                name_set = set()
                deduped = []
                for n in graph_problem_names:
                    short = n[:60]
                    if short not in name_set:
                        name_set.add(short)
                        deduped.append(n)
                problem_hints = " OR ".join(
                    f"(symptom ILIKE '%{name}%')"
                    for name in deduped
                )
                modified = f"{modified} Graph analysis found these specific problems: {', '.join(deduped)}. WAJIB filter untuk setiap problem tsb dengan: ({problem_hints})."

            if has_model_entities and entities:
                model_names = [e["canonical_name"] for e in entities if e["entity_type"] == "model"]
                if model_names:
                    model_filter_hint = " OR ".join(
                        f"(machine_model ILIKE '%{m}%' OR model_family ILIKE '%{m}%')"
                        for m in model_names
                    )
                    modified = f"{modified} WAJIB filter model unit dengan: ({model_filter_hint})."

            if not account_hint:
                modified = f"{modified} JANGAN GUNAKAN filter account_account_name — user tidak menyebut nama account/customer apapun. HANYA gunakan filter yang sudah diperintahkan di atas!"

            if "tunjukkan emr" in query.lower() or "tampilkan emr" in query.lower() or "list emr" in query.lower():
                modified = f"{modified} User meminta list EMR. WAJIB HANYA SELECT emr_name, ppi_external_id, ppi_improvement_name. JANGAN GUNAKAN FUNGSI AGREGASI COUNT() SAMA SEKALI!"
            if "ppi" in query.lower():
                modified = f"{modified} JANGAN PERNAH menambahkan filter 'ppi_external_id IS NOT NULL' pada klausa WHERE! Hitung semua masalah secara normal terlepas apakah memiliki PPI atau tidak."

            logger.info(f"Vanna prompt: {modified}")
            vn = get_vanna()
            sql = vn.generate_sql(modified, allow_llm_to_see_data=False)

            if not sql:
                span.set_attribute("sql_generated", False)
                return {"answer": "Could not generate SQL for the query.", "chunks": None, "sql": None, "sql_data": None}

            span.set_attribute("sql_generated", True)
            span.set_attribute("sql_query", sql)
            logger.info(f"Vanna generated SQL: {sql}")

            sql = re.sub(
                r"\bAND\s+\S*\.?account_account_name\s*=\s*'[^']*'",
                "",
                sql,
                flags=re.IGNORECASE,
            ).strip()
            sql = re.sub(
                r"\bAND\s+\S*\.?account_account_name\s+ILIKE\s*'[^']*'",
                "",
                sql,
                flags=re.IGNORECASE,
            ).strip()
            sql = re.sub(r"\s+WHERE\s+(AND|OR)\s+", " WHERE ", sql, flags=re.IGNORECASE)

            if should_inject_community:
                if not re.search(r'\bcommunity_id\b', sql, re.IGNORECASE):
                    sql = _inject_sql_condition(sql, cid_hint)
            else:
                sql = _strip_community_filter(sql)
                
            if site_hint and site_hint not in sql:
                logger.warning(f"site_hint '{site_hint}' not found in Vanna SQL — prompt may have been ignored")

            if account_hint and account_hint not in sql:
                logger.info(f"Re-injecting account_hint into SQL: {account_hint}")
                sql = _inject_sql_condition(sql, account_hint)

            # Force-inject graph_problem_names ILIKE filters if Vanna ignored the prompt
            graph_problem_names = ctx.get("graph_problem_names")
            if graph_problem_names and not any(
                re.search(re.escape(n[:30]), sql, re.IGNORECASE)
                for n in graph_problem_names
            ):
                deduped_names = list(dict.fromkeys(graph_problem_names))
                ilike_parts = " OR ".join(
                    f"(symptom ILIKE '%{n[:80]}%' OR caused_of_problem ILIKE '%{n[:80]}%' OR subjects ILIKE '%{n[:80]}%')"
                    for n in deduped_names
                )
                sql = _inject_sql_condition(sql, ilike_parts)
                logger.info(f"Post-injected graph_problem_names ILIKE into SQL: {deduped_names[:5]}...")

            sql = _expand_ilipe_patterns(sql)
            sql = _adjust_small_limit(sql)

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

            resolved_info = [
                {"mention": e["mention"], "canonical_name": e["canonical_name"],
                 "type": e["entity_type"], "score": e["score"]}
                for e in entities
            ] if entities else None

            ppi_ids = []
            if df is not None and not df.empty and "ppi_external_id" in df.columns:
                ppi_ids = df["ppi_external_id"].dropna().unique().astype(str).tolist()

            ppi_info, ppi_list = _enrich_with_ppi(record_identifiers, ppi_ids=ppi_ids, query=query)
            if ppi_info:
                result_str += ppi_info

            return {"answer": result_str, "chunks": None, "sql": sql, "sql_data": sql_data, "resolved_entities": resolved_info, "ppi_links": ppi_list or None}
        except Exception as e:
            import traceback
            traceback.print_exc()
            span.record_exception(e)
            logger.error(f"Error in ask_emr_database: {e}")
            return {"answer": f"Error querying database: {e}", "chunks": None, "sql": None, "sql_data": None}


@register_tool(args_schema=QueryArgs)
def search_emr_records(query: str, resolved_context: Optional[dict] = None) -> Dict[str, Any]:
    from src.services.telemetry import tracer
    with tracer.start_as_current_span("search_emr_records") as span:
        span.set_attribute("tool_query", query)
        logger.info(f"Using tool search_emr_records for query: {query}")
        try:
            ctx = resolved_context if resolved_context is not None else _build_full_context(query)
            site_hint = ctx.get("site_hint")
            account_hint = ctx.get("account_hint")

            if site_hint:
                logger.info(f"Site resolution (Graph): {site_hint}")
            span.set_attribute("has_site_hint", site_hint is not None)
            if account_hint:
                logger.info(f"Account resolution (Graph): {account_hint}")
                _acct_names_in_hint = re.findall(r"'([^']+)'", account_hint)
                _valid_names = [n for n in _acct_names_in_hint if n.lower() in query.lower()]
                if not _valid_names:
                    logger.warning(f"search_emr_records: account hint '{account_hint}' has no match in query — treating as false positive")
                    account_hint = None
            span.set_attribute("has_account_hint", account_hint is not None)

            resolver = _get_entity_resolver()
            result = resolver.search_emr_records(query, site_hint=site_hint, account_hint=account_hint, resolved_ctx=ctx)
            canonical_names = result.get("canonical_names", [])
            neo4j_records = result.get("emr_records", [])
            entities = result.get("entities", [])

            if not neo4j_records:
                return {"answer": "Tidak ditemukan EMR records yang cocok dengan deskripsi tersebut.", "emr_records": [], "entities": entities}

            emr_names = [r["emr_name"] for r in neo4j_records if r.get("emr_name")]

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
def analyze_smr(query: str, resolved_context: Optional[dict] = None) -> Dict[str, Any]:
    from src.services.telemetry import tracer
    with tracer.start_as_current_span("analyze_smr") as span:
        span.set_attribute("tool_query", query)
        logger.info(f"Using tool analyze_smr for query: {query}")
        try:
            ctx = resolved_context if resolved_context is not None else _build_full_context(query)
            symptom_cids = ctx.get("symptom_community_ids", [])
            canonical_names = ctx.get("canonical_names", [])
            site_hint = ctx.get("site_hint")
            account_hint = ctx.get("account_hint")
            entities = ctx.get("entities", [])
            model_entities = ctx.get("model_entities", [])

            if site_hint:
                logger.info(f"SMR site resolution: {site_hint}")
            if account_hint:
                logger.info(f"SMR account resolution: {account_hint}")
                _acct_names_in_hint = re.findall(r"'([^']+)'", account_hint)
                _valid_names = [n for n in _acct_names_in_hint if n.lower() in query.lower()]
                if not _valid_names:
                    logger.warning(f"SMR account hint '{account_hint}' has no match in original query — treating as false positive")
                    account_hint = None

            from sqlalchemy import create_engine, text
            from src.config import settings

            engine = create_engine(settings.readonly_postgres_url)
            with engine.connect() as conn:
                site_cond = f" AND ({site_hint})" if site_hint else ""
                account_cond = f" AND ({account_hint})" if account_hint else ""
                
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
                    ilike_names = list(set(canonical_names + ctx.get("expanded_names", [])))
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
                    # Fallback: extract symptom-like keywords from the query directly
                    fallback_terms = _extract_symptom_keywords(query)
                    if fallback_terms:
                        ilike_names = fallback_terms
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
                        logger.info(f"SMR entity fallback — extracted ILIKE terms: {fallback_terms}")
                    else:
                        return {"answer": "Tidak dapat mengidentifikasi masalah dari query.", "smr_data": [], "count": 0}

                sql = _expand_ilipe_patterns(sql)
                sql = sql.rstrip().rstrip(';')
                if not re.search(r'\bLIMIT\b', sql, re.IGNORECASE) and re.search(r'\bsymptom\b', sql, re.IGNORECASE):
                    sql += " LIMIT 500"

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
                "entities": entities,
            }

        except Exception as e:
            span.record_exception(e)
            logger.error(f"Error in analyze_smr: {e}")
            return {"answer": f"Error analyzing SMR data: {e}", "smr_data": [], "count": 0}
