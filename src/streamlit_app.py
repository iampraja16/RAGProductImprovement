import streamlit as st
import requests
import json
import os
import pandas as pd

API_URL = os.getenv("API_URL", "http://localhost:8000")
API_KEY = os.getenv("API_KEY", "")

def clean_markdown_content(text: str) -> str:
    if not isinstance(text, str):
        return text
    
    # 1. Standardize line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    
    # 2. Strip all triple backtick code block lines (to prevent rendering as monospace boxes / canvas)
    # and unindent lines that have 4 or more leading spaces to prevent Indented Code Blocks.
    lines = text.split("\n")
    cleaned_lines = []
    
    for line in lines:
        stripped_line = line.strip()
        
        # If it's a code block marker, skip it entirely
        if stripped_line.startswith("```"):
            continue
            
        leading_whitespace = len(line) - len(line.lstrip())
        if leading_whitespace >= 4:
            cleaned_lines.append(line.lstrip())
        else:
            cleaned_lines.append(line)
            
    return "\n".join(cleaned_lines).strip()


import re

def inject_ppi_links(text: str, ppi_links: list) -> str:
    """Inject markdown hyperlinks for PPI identifiers in the LLM-generated text.

    Handles variant formats the LLM may produce for the same PPI ID:
      - PPI.000119          (canonical format stored in Neo4j)
      - PPI-000119          (dash separator)
      - Techcare.PPI.000119 (with prefix)
      - PPI 000119          (space separator)

    The match is anchored to the numeric suffix only, so all variants are caught.
    If salesforce_url is present  -> renders as [PPI.000119](url).
    If salesforce_url is missing  -> renders as **PPI.000119** (bold, no link).
    """
    if not ppi_links or not text:
        return text

    for ppi in ppi_links:
        ext_id = ppi.get("external_id")
        sf_url = ppi.get("salesforce_url") or ""
        if not ext_id:
            continue

        # Extract the numeric part (e.g. "000119" from "PPI.000119")
        num_part = re.escape(ext_id.split(".")[-1])

        # Match variants: optional 'Techcare.' prefix, then 'PPI' + separator + number
        # Negative lookbehind/ahead prevents double-linking an already-linked ID
        pattern = rf"(?<!\[)(?:Techcare\.)?PPI[.\- ]?{num_part}(?!\]\()"

        if sf_url:
            replacement = f"[{ext_id}]({sf_url})"
        else:
            replacement = f"**{ext_id}**"

        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    return text


def render_ppi_references(ppi_links: list):
    """Render a dedicated PPI References section as clickable markdown links.

    This is a reliable fallback: PPI links always appear here regardless of
    how the LLM phrased the answer, since inject_ppi_links() depends on the
    LLM reproducing a recognizable PPI ID format in its output text.
    """
    if not ppi_links:
        return
    st.markdown("#### \U0001f517 PPI References")
    for ppi in ppi_links:
        ext_id = ppi.get("external_id", "")
        sf_url = ppi.get("salesforce_url", "")
        name = ppi.get("improvement_name", "")
        if sf_url:
            st.markdown(f"- [{ext_id}]({sf_url}) \u2014 {name}")
        else:
            st.markdown(f"- **{ext_id}** \u2014 {name}")



st.set_page_config(
    page_title="EMR Fault Analyzer",
    page_icon="EMR",
    layout="wide"
)

# ===================================================================
# Helper: Graph Visualization (streamlit-agraph)
# ===================================================================

def render_graph_visualization(graph_traversal: dict):
    """Render an interactive graph from graph_traversal data using streamlit-agraph."""
    try:
        from streamlit_agraph import agraph, Node, Edge, Config
    except ImportError:
        st.warning("Install streamlit-agraph untuk visualisasi interaktif: pip install streamlit-agraph")
        return

    nodes = []
    edges = []
    added_nodes = set()
    
    # 1. Provide a generic dynamic rendering based on 'raw_rows' if available
    raw_rows = graph_traversal.get("raw_rows", [])
    seed_entities = graph_traversal.get("entities_found", graph_traversal.get("seed_entities", []))

    # Color mapping for different node labels
    color_map = {
        "SymptomPattern": "#FF6B6B",
        "ProblemCluster": "#FF9F43",
        "Community": "#FF9F43",
        "RootCausePattern": "#FFD200",
        "ActionPattern": "#1DD1A1",
        "Part": "#5F27CD",
        "MachineModel": "#48dbfb"
    }

    if raw_rows:
        for row in raw_rows:
            e_name = str(row.get("entity", "Unknown"))
            n_name = str(row.get("neighbor", ""))
            rel = str(row.get("relation", ""))
            n_label = str(row.get("n_label", "Entity"))
            
            # Source Node (usually the seed entity)
            if e_name not in added_nodes:
                is_seed = e_name in seed_entities
                nodes.append(Node(
                    id=e_name,
                    label=e_name[:25] + ("..." if len(e_name)>25 else ""),
                    title=f"{e_name}",
                    size=25 if is_seed else 20,
                    color="#FF6B6B" if is_seed else "#a4b0be",
                    shape="ellipse" if is_seed else "dot",
                ))
                added_nodes.add(e_name)
            
            # Target Node
            if n_name and n_name != "None":
                if n_name not in added_nodes:
                    node_color = color_map.get(n_label, "#ced6e0")
                    nodes.append(Node(
                        id=n_name,
                        label=n_name[:25] + ("..." if len(n_name)>25 else ""),
                        title=f"{n_label}\n{n_name}",
                        size=20,
                        color=node_color,
                        shape="box",
                    ))
                    added_nodes.add(n_name)
                
                # Edge
                edges.append(Edge(
                    source=e_name,
                    target=n_name,
                    label=rel,
                    color="#aaaaaa",
                    font={"size": 10},
                ))
    else:
        # Fallback if no raw_rows (e.g. drift mode doesn't return raw_rows yet)
        for seed in seed_entities:
            nodes.append(Node(
                id=seed, label=seed[:25], title="Seed Entity", size=25, color="#FF6B6B", shape="ellipse"
            ))

    config = Config(
        width="100%",
        height=450,
        directed=True,
        physics=True,  # Turn physics on for dynamic graphing
        hierarchical=True,  
        nodeHighlightBehavior=True,
        highlightColor="#F7F7F7",
        collapsible=False,
    )

    agraph(nodes=nodes, edges=edges, config=config)


# ===================================================================
# Helper: Reasoning Trace
# ===================================================================

TOOL_LABELS = {
    "ask_emr_graph":              "Knowledge Graph Search",
    "ask_emr_database":           "SQL Database Query",
    "generate_executive_summary": "Executive Summary Generator",
    "analyze_smr":                "SMR Analysis",
}

def render_reasoning_trace(steps: list, timing_ms: dict = None, cache_hit: str = None):
    """Render the agent's step-by-step thinking process."""
    with st.expander("Agent Thinking Process", expanded=False):
        if cache_hit:
            st.success(f"Cache Hit ({cache_hit}) — Response served from cache, no pipeline executed.")
            if timing_ms:
                st.caption(f"Total: {timing_ms.get('total_ms', 0):.0f}ms")
            return

        if not steps:
            st.info("No step data available.")
            return

        # Timing overview
        if timing_ms:
            cols = st.columns(4)
            cols[0].metric("Embedding", f"{timing_ms.get('embedding_ms', 0):.0f}ms")
            cols[1].metric("Cache Check", f"{timing_ms.get('cache_check_ms', 0):.0f}ms")
            cols[2].metric("Pipeline", f"{timing_ms.get('agent_pipeline_ms', 0):.0f}ms")
            cols[3].metric("Total", f"{timing_ms.get('total_ms', 0):.0f}ms")
            st.divider()

        # Step-by-step trace
        for i, step in enumerate(steps):
            step_type = step.get("step", "unknown")

            if step_type == "llm_routing":
                tool  = step.get("tool_selected")
                label = TOOL_LABELS.get(tool, tool) if tool else "Direct Answer (no tool)"
                dur   = step.get("duration_ms", 0)

                if step.get("direct_answer"):
                    st.markdown(f"**Step {i+1}: LLM Routing** ({dur:.0f}ms)")
                    st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;Decided to answer directly (no tool needed)")
                else:
                    st.markdown(f"**Step {i+1}: LLM Routing** ({dur:.0f}ms)")
                    st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;Selected tool: **{label}**")
                    if step.get("tool_args"):
                        st.caption(f"&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Query passed: `{str(step['tool_args'])[:120]}`")

            elif step_type == "tool_exec":
                tool   = step.get("tool", "unknown")
                label  = TOOL_LABELS.get(tool, tool)
                dur    = step.get("duration_ms", 0)
                status = step.get("status", "ok")

                if status == "error":
                    st.markdown(f"**Step {i+1}: {label}** — Error")
                    st.caption(f"&nbsp;&nbsp;&nbsp;&nbsp;Error: {step.get('error', 'unknown')}")
                else:
                    retrieved = step.get("retrieved_chunks", 0)
                    has_graph = step.get("has_graph", False)
                    has_sql   = step.get("has_sql", False)

                    result_tags = []
                    if has_graph:  result_tags.append("graph traversal")
                    if has_sql:    result_tags.append("SQL query")
                    if retrieved:  result_tags.append(f"{retrieved} doc chunks")
                    result_str = ", ".join(result_tags) if result_tags else "context"

                    st.markdown(f"**Step {i+1}: {label}** ({dur:.0f}ms)")
                    st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;Retrieved: **{result_str}**")

        st.divider()
        st.caption("Steps above show how the agent chose and executed retrieval tools before generating the final answer.")


# ===================================================================
# Sidebar
# ===================================================================
with st.sidebar:
    st.header("EMR Fault Analyzer")
    st.markdown("Hybrid RAG + SQL agent for Equipment Maintenance Records.")

    # Health check
    try:
        res = requests.get(f"{API_URL}/health", timeout=5)
        if res.status_code == 200:
            health_data = res.json()
            overall = health_data.get("status", "unknown")
            if overall == "healthy":
                st.success("Backend Connected")
            else:
                st.warning("Backend Degraded")

            with st.expander("Service Status"):
                for svc, status in health_data.get("services", {}).items():
                    indicator = "OK" if status in ("ok", "loaded") else "ERROR"
                    st.markdown(f"**{svc}**: {indicator} — `{status}`")
        else:
            st.error("Backend Error")
    except requests.exceptions.ConnectionError:
        st.error("Backend Disconnected")

    st.divider()

    st.subheader("Example Questions")
    examples = [
        "Apa penyebab paling umum hydraulic leak pada PC200?",
        "Tampilkan 5 model yang paling sering rusak.",
        "Buat laporan executive summary untuk HD465.",
    ]
    for ex in examples:
        if st.button(ex, use_container_width=True):
            st.session_state.messages.append({"role": "user", "content": ex})
            st.rerun()

    if st.button("Clear Chat History", type="primary", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

    st.divider()
    st.subheader("Settings")
    st.session_state.retrieval_mode = st.selectbox(
        "Graph Retrieval Mode",
        options=["drift", "local", "global"],
        index=0,
        help="DRIFT: Detail + Context (Default)\nLocal: Specific entities only\nGlobal: High-level community trends"
    )

    # Cache stats
    st.divider()
    st.subheader("Cache Stats")
    try:
        stats_res = requests.get(f"{API_URL}/cache/stats", timeout=3)
        if stats_res.status_code == 200:
            stats = stats_res.json()
            sem = stats.get("semantic_cache", {})
            emb = stats.get("embedding_cache", {})
            hit_rate = sem.get("hit_rate", 0)
            emb_rate = emb.get("hit_rate", 0)
            st.metric("Semantic Cache Hit Rate", f"{hit_rate:.0%}",
                      help=f"Hits: {sem.get('hits',0)} / Misses: {sem.get('misses',0)}")
            st.metric("Embedding Cache Hit Rate", f"{emb_rate:.0%}",
                      help=f"Entries cached: {emb.get('cache_entries',0)}")
        if st.button("Invalidate Cache", use_container_width=True):
            headers = {"X-API-Key": API_KEY}
            requests.post(f"{API_URL}/cache/invalidate", json={"level": "all"}, headers=headers, timeout=5)
            st.success("Cache cleared!")
    except Exception:
        st.caption("Cache stats unavailable.")

# ===================================================================
# Main Chat UI
# ===================================================================

if "messages" not in st.session_state:
    st.session_state.messages = []

st.title("Maintenance Copilot")
st.markdown("Tanya tentang penyebab masalah, gejala, atau statistik jumlah kerusakan unit.")

# --- Chat History ---
def render_assistant_answer(content: str, ppi_links: list = None):
    content = inject_ppi_links(content, ppi_links)
    divider = "--- EVIDENCE/PROVENANCE ---"
    if divider in content:
        parts = content.split(divider)
        narrative = parts[0].strip()
        evidence = parts[1].strip()

        st.markdown("#### Answer")
        st.markdown(clean_markdown_content(narrative))
        st.markdown("#### Evidence Section")
        st.info(clean_markdown_content(evidence))
    else:
        st.markdown("#### Answer")
        st.markdown(clean_markdown_content(content))

    # Dedicated PPI section hidden as requested by user
    pass


# --- Chat History ---
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        if message["role"] == "user":
            st.markdown(clean_markdown_content(message["content"]))
        else:
            render_assistant_answer(message["content"], message.get("ppi_links"))

        if message["role"] == "assistant":
            # Reasoning trace
            if message.get("steps") is not None or message.get("timing_ms"):
                render_reasoning_trace(
                    steps=message.get("steps", []),
                    timing_ms=message.get("timing_ms"),
                    cache_hit=message.get("cache_hit"),
                )

            # Graph visualization
            if message.get("graph_traversal"):
                st.markdown("#### Graph Section")
                render_graph_visualization(message["graph_traversal"])

            # SMR scatter plot
            if message.get("smr_data"):
                st.markdown("#### SMR Distribution")
                try:
                    import plotly.express as px
                    df_smr = pd.DataFrame(message["smr_data"])
                    if not df_smr.empty and "smr" in df_smr.columns:
                        df_smr["smr"] = pd.to_numeric(df_smr["smr"], errors="coerce")
                        df_smr = df_smr.dropna(subset=["smr"])
                        fig = px.scatter(
                            df_smr, x="created_date", y="smr",
                            hover_data=["emr_name", "symptom", "machine_model"],
                            title=f"SMR Distribution ({len(df_smr)} data points)",
                            labels={"smr": "Service Meter Reading (hours)", "created_date": "Date"},
                            opacity=0.7,
                        )
                        fig.update_traces(marker=dict(size=8))
                        st.plotly_chart(fig, use_container_width=True)
                except Exception as e:
                    st.caption(f"Scatter plot unavailable: {e}")

            if message.get("sql"):
                st.markdown("#### SQL Section")
                st.code(message["sql"], language="sql")

            if message.get("sql_data"):
                st.markdown("#### Database Table (PostgreSQL)")
                st.dataframe(pd.DataFrame(message["sql_data"]), use_container_width=True)

            if message.get("chunks"):
                with st.expander("View Raw Retrieved Chunks"):
                    for i, chunk in enumerate(message["chunks"]):
                        st.markdown(f"**Document {i+1}**")
                        st.markdown(chunk)
                        st.divider()

            if message.get("token_usage"):
                tu = message["token_usage"]
                with st.expander("📊 Token Usage & Cost", expanded=False):
                    cols = st.columns(4)
                    cols[0].metric("Prompt Tokens", f"{tu.get('prompt_tokens', 0):,}")
                    cols[1].metric("Completion Tokens", f"{tu.get('completion_tokens', 0):,}")
                    cols[2].metric("Total Tokens", f"{tu.get('total_tokens', 0):,}")
                    cols[3].metric("Estimated Cost", f"${tu.get('estimated_cost_usd', 0.0):.5f}")
                    st.caption(f"Estimation method: `{tu.get('estimation_method', 'unknown')}`")

# --- Chat Input ---
if prompt := st.chat_input("Tanya sesuatu tentang EMR..."):
    st.session_state.messages.append({"role": "user", "content": prompt})

    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        status_box = st.status("Analyzing...", expanded=True)
        with status_box:
            st.write("Connecting to backend...")

        try:
            history_for_api = st.session_state.messages[:-1]
            # Since the API uses Agent calling tools, we prepend a system instruction
            # for the agent if we want to force a mode, but for simplicity, we pass it via query context
            mode_context = f"[System: Use '{st.session_state.retrieval_mode}' mode if using ask_emr_graph] "
            payload = {"query": mode_context + prompt, "chat_history": history_for_api}

            # Call FastAPI streaming chat endpoint
            headers = {"X-API-Key": API_KEY}
            response = requests.post(f"{API_URL}/chat/stream", json=payload, stream=True, headers=headers, timeout=300)

            if response.status_code == 200:
                answer = ""
                sql = None
                sql_data = None
                chunks = []
                graph_traversal = None
                smr_data = None
                ppi_links = None
                steps = []
                timing_ms = None
                cache_hit = None
                token_usage = None

                # Create placeholder for streaming text
                message_placeholder = st.empty()

                # Process streaming lines
                for line in response.iter_lines():
                    if not line:
                        continue
                    
                    data = json.loads(line.decode("utf-8"))
                    chunk_type = data.get("type")

                    if chunk_type == "status":
                        content = data.get("content", "")
                        with status_box:
                            st.write(content)

                    elif chunk_type == "tool_data":
                        sql = data.get("sql")
                        sql_data = data.get("sql_data")
                        graph_traversal = data.get("graph_traversal")
                        chunks = data.get("chunks", [])
                        smr_data = data.get("smr_data")
                        ppi_links = data.get("ppi_links")

                    elif chunk_type == "token":
                        token = data.get("content", "")
                        answer += token
                        
                        # Display cumulative streaming text with sections
                        divider = "--- EVIDENCE/PROVENANCE ---"
                        display_text = inject_ppi_links(answer, ppi_links)
                        if divider in display_text:
                            parts = display_text.split(divider)
                            narrative = parts[0].strip()
                            evidence = parts[1].strip()
                            message_placeholder.markdown(f"#### Answer\n\n{clean_markdown_content(narrative)}\n\n#### Evidence Section\n\n{clean_markdown_content(evidence)}▌")
                        else:
                            message_placeholder.markdown(f"#### Answer\n\n{clean_markdown_content(display_text)}▌")

                    elif chunk_type == "done":
                        steps = data.get("steps", [])
                        timing_ms = data.get("timing_ms", None)
                        cache_hit = data.get("cache_hit", None)
                        if data.get("smr_data"):
                            smr_data = data.get("smr_data")
                        # ppi_links may arrive via done if not in tool_data
                        if data.get("ppi_links") and not ppi_links:
                            ppi_links = data.get("ppi_links")
                        
                        # Remove cursor at completion
                        divider = "--- EVIDENCE/PROVENANCE ---"
                        display_text = inject_ppi_links(answer, ppi_links)
                        if divider in display_text:
                            parts = display_text.split(divider)
                            narrative = parts[0].strip()
                            evidence = parts[1].strip()
                            message_placeholder.markdown(f"#### Answer\n\n{clean_markdown_content(narrative)}\n\n#### Evidence Section\n\n{clean_markdown_content(evidence)}")
                        else:
                            message_placeholder.markdown(f"#### Answer\n\n{clean_markdown_content(display_text)}")

                    elif chunk_type == "metadata":
                        token_usage = data.get("content", {}).get("token_usage")

                    elif chunk_type == "error":
                        st.error(data.get("content"))
                        answer = f"Error: {data.get('content')}"

                # Update status box
                with status_box:
                    if cache_hit:
                        st.write(f"Cache hit ({cache_hit}) — skipping pipeline")
                    else:
                        for step in steps:
                            if step.get("step") == "llm_routing":
                                tool  = step.get("tool_selected")
                                label = TOOL_LABELS.get(tool, tool) if tool else "direct answer"
                                st.write(f"LLM selected tool: {label} ({step.get('duration_ms',0):.0f}ms)")
                            elif step.get("step") == "tool_exec":
                                tool   = step.get("tool", "")
                                label  = TOOL_LABELS.get(tool, tool)
                                dur    = step.get("duration_ms", 0)
                                result = "OK" if step.get("status") == "ok" else "Error"
                                st.write(f"{result} — {label} ({dur:.0f}ms)")
                            elif step.get("step") == "final_synthesis":
                                st.write(f"Final synthesis generated ({step.get('duration_ms', 0):.0f}ms)")

                    if timing_ms:
                        st.write(f"Done in {timing_ms.get('total_ms', 0):.0f}ms")
                    elif steps:
                        total_dur = sum(s.get("duration_ms", 0) for s in steps)
                        st.write(f"Done in {total_dur:.0f}ms")

                status_box.update(label="Analysis complete", state="complete", expanded=False)

                # Reasoning trace (collapsible)
                render_reasoning_trace(steps=steps, timing_ms=timing_ms, cache_hit=cache_hit)

                # Interactive graph visualization
                if graph_traversal:
                    st.markdown("#### Graph Section")
                    render_graph_visualization(graph_traversal)

                if sql:
                    st.markdown("#### SQL Section")
                    st.code(sql, language="sql")

                if sql_data:
                    st.markdown("#### Database Table (PostgreSQL)")
                    st.dataframe(pd.DataFrame(sql_data), use_container_width=True)

                if smr_data:
                    st.markdown("#### SMR Distribution")
                    try:
                        import plotly.express as px
                        df = pd.DataFrame(smr_data)
                        if not df.empty and "smr" in df.columns:
                            df["smr"] = pd.to_numeric(df["smr"], errors="coerce")
                            df = df.dropna(subset=["smr"])
                            fig = px.scatter(
                                df, x="created_date", y="smr",
                                hover_data=["emr_name", "symptom", "machine_model"],
                                title=f"SMR Distribution ({len(df)} data points)",
                                labels={"smr": "Service Meter Reading (hours)", "created_date": "Date"},
                                opacity=0.7,
                            )
                            fig.update_traces(marker=dict(size=8))
                            st.plotly_chart(fig, use_container_width=True)
                    except Exception as e:
                        st.caption(f"Scatter plot unavailable: {e}")

                if chunks:
                    with st.expander("View Raw Retrieved Chunks"):
                        for i, chunk in enumerate(chunks):
                            st.markdown(f"**Document {i+1}**")
                            st.markdown(chunk)
                            st.divider()

                # Dedicated PPI section hidden as requested by user
                pass

                # Token Usage Panel
                if token_usage:
                    with st.expander("📊 Token Usage & Cost", expanded=False):
                        cols = st.columns(4)
                        cols[0].metric("Prompt Tokens", f"{token_usage.get('prompt_tokens', 0):,}")
                        cols[1].metric("Completion Tokens", f"{token_usage.get('completion_tokens', 0):,}")
                        cols[2].metric("Total Tokens", f"{token_usage.get('total_tokens', 0):,}")
                        cols[3].metric("Estimated Cost", f"${token_usage.get('estimated_cost_usd', 0.0):.5f}")
                        st.caption(f"Estimation method: `{token_usage.get('estimation_method', 'unknown')}`")

                # Save to session state
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": answer,
                    "sql": sql,
                    "sql_data": sql_data,
                    "chunks": chunks,
                    "graph_traversal": graph_traversal,
                    "smr_data": smr_data,
                    "ppi_links": ppi_links,
                    "steps": steps,
                    "timing_ms": timing_ms,
                    "cache_hit": cache_hit,
                    "token_usage": token_usage,
                })
            else:
                status_box.update(label="Error", state="error")
                error_msg = f"API Error: {response.text}"
                st.error(error_msg)
                st.session_state.messages.append({"role": "assistant", "content": error_msg})

        except requests.exceptions.ConnectionError:
            status_box.update(label="Disconnected", state="error")
            error_msg = "Could not connect to the backend API. Please ensure the FastAPI server is running on port 8000."
            st.error(error_msg)
            st.session_state.messages.append({"role": "assistant", "content": error_msg})
        except Exception as e:
            status_box.update(label="Error", state="error")
            error_msg = f"An error occurred: {str(e)}"
            st.error(error_msg)
            st.session_state.messages.append({"role": "assistant", "content": error_msg})
