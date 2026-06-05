import streamlit as st
import requests
import json
import os
import pandas as pd

API_URL = "http://localhost:8000"

st.set_page_config(
    page_title="EMR Fault Analyzer",
    page_icon="⚙️",
    layout="wide"
)

# Initialize session state
if "messages" not in st.session_state:
    st.session_state.messages = []

# Sidebar
with st.sidebar:
    st.header("EMR Fault Analyzer")
    st.markdown("Hybrid RAG + SQL agent for Equipment Maintenance Records.")
    
    # Status Check
    try:
        res = requests.get(f"{API_URL}/health", timeout=5)
        if res.status_code == 200:
            st.success("✅ Backend Connected")
        else:
            st.error("❌ Backend Error")
    except requests.exceptions.ConnectionError:
        st.error("❌ Backend Disconnected (Start API server on port 8000)")
        
    st.divider()
    
    st.subheader("📊 Clusters Preview")
    cluster_file = os.path.join("output", "cluster_summary.json")
    if os.path.exists(cluster_file):
        with open(cluster_file, "r") as f:
            clusters = json.load(f)
            st.write(f"Total Clusters: {len(clusters)}")
            # Just show a few for preview
            preview = clusters[:3]
            st.json(preview)
            if len(clusters) > 3:
                st.caption(f"... and {len(clusters) - 3} more")
    else:
        st.info("No clustering data found. Run notebook 1.")

    st.divider()
    st.subheader("💡 Example Questions")
    examples = [
        "Apa penyebab paling umum hydraulic leak pada PC200?",
        "Tampilkan 5 model yang paling sering rusak.",
        "Buat laporan executive summary untuk HD465."
    ]
    for ex in examples:
        if st.button(ex, use_container_width=True):
            st.session_state.messages.append({"role": "user", "content": ex})
            st.rerun()
            
    if st.button("🗑️ Clear Chat History", type="primary", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

# Main Chat UI
st.title("🚜 Maintenance Copilot")
st.markdown("Tanya tentang penyebab masalah, gejala, atau statistik jumlah kerusakan unit.")

# Display chat history
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        
        # Display extra info if available (only for assistant messages)
        if message["role"] == "assistant":
            if "graph_traversal" in message and message["graph_traversal"]:
                gt = message["graph_traversal"]
                with st.expander("🔗 View Graph Traversal Path", expanded=True):
                    st.markdown("### 🛠️ Causal Resolution Path")
                    st.markdown(
                        f"**Symptom Pattern:** `{gt.get('symptom_matched')}` "
                        f"(Similarity: **{gt.get('similarity', 0):.0%}**)"
                    )
                    st.markdown(f"➔ **Problem Cluster:** `{gt.get('problem_cluster')}` (ID: `{gt.get('cluster_id')}`)")
                    st.markdown("➔ **Recommended Actions:**")
                    for a in gt.get("actions", [])[:5]:
                        st.markdown(f"- **{a['action']}** ({a['frequency']} cases)")
                        valid_parts = [p for p in a.get("parts", []) if p.get("part_no") and p["part_no"] != "None"]
                        if valid_parts:
                            part_list = ", ".join([f"{p.get('description', '')} ({p['part_no']})" for p in valid_parts[:3]])
                            st.caption(f"  *Parts needed:* {part_list}")
            if "sql" in message and message["sql"]:
                with st.expander("🔍 View SQL Query"):
                    st.code(message["sql"], language="sql")
            if "chunks" in message and message["chunks"]:
                with st.expander("📚 View Retrieved Context"):
                    for i, chunk in enumerate(message["chunks"]):
                        st.markdown(f"**Document {i+1}**")
                        st.markdown(chunk)
                        st.divider()

# Chat Input
if prompt := st.chat_input("Tanya sesuatu tentang EMR..."):
    # Add user message to state
    st.session_state.messages.append({"role": "user", "content": prompt})
    
    # Display user message immediately
    with st.chat_message("user"):
        st.markdown(prompt)
        
    # Send to API and show loading
    with st.chat_message("assistant"):
        with st.spinner("Analyzing data..."):
            try:
                # Prepare history for the API
                history_for_api = st.session_state.messages[:-1] # Exclude the current prompt
                
                payload = {
                    "query": prompt,
                    "chat_history": history_for_api
                }
                
                response = requests.post(f"{API_URL}/chat", json=payload, timeout=300)
                
                if response.status_code == 200:
                    data = response.json()
                    answer = data.get("answer", "")
                    sql = data.get("sql", None)
                    chunks = data.get("chunks", [])
                    graph_traversal = data.get("graph_traversal", None)
                    
                    st.markdown(answer)
                    
                    if graph_traversal:
                        with st.expander("🔗 View Graph Traversal Path", expanded=True):
                            st.markdown("### 🛠️ Causal Resolution Path")
                            st.markdown(
                                f"**Symptom Pattern:** `{graph_traversal.get('symptom_matched')}` "
                                f"(Similarity: **{graph_traversal.get('similarity', 0):.0%}**)"
                            )
                            st.markdown(f"➔ **Problem Cluster:** `{graph_traversal.get('problem_cluster')}` (ID: `{graph_traversal.get('cluster_id')}`)")
                            st.markdown("➔ **Recommended Actions:**")
                            for a in graph_traversal.get("actions", [])[:5]:
                                st.markdown(f"- **{a['action']}** ({a['frequency']} cases)")
                                valid_parts = [p for p in a.get("parts", []) if p.get("part_no") and p["part_no"] != "None"]
                                if valid_parts:
                                    part_list = ", ".join([f"{p.get('description', '')} ({p['part_no']})" for p in valid_parts[:3]])
                                    st.caption(f"  *Parts needed:* {part_list}")
                    if sql:
                        with st.expander("🔍 View SQL Query"):
                            st.code(sql, language="sql")
                    if chunks:
                        with st.expander("📚 View Retrieved Context"):
                            for i, chunk in enumerate(chunks):
                                st.markdown(f"**Document {i+1}**")
                                st.markdown(chunk)
                                st.divider()
                                
                    # Save to state
                    st.session_state.messages.append({
                        "role": "assistant", 
                        "content": answer,
                        "sql": sql,
                        "chunks": chunks,
                        "graph_traversal": graph_traversal
                    })
                else:
                    error_msg = f"API Error: {response.text}"
                    st.error(error_msg)
                    st.session_state.messages.append({"role": "assistant", "content": error_msg})
                    
            except requests.exceptions.ConnectionError:
                error_msg = "Could not connect to the backend API. Please ensure the FastAPI server is running on port 8000."
                st.error(error_msg)
                st.session_state.messages.append({"role": "assistant", "content": error_msg})
            except Exception as e:
                error_msg = f"An error occurred: {str(e)}"
                st.error(error_msg)
                st.session_state.messages.append({"role": "assistant", "content": error_msg})
