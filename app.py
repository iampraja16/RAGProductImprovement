"""
app.py — Streamlit Frontend for RAG Fault Analyzer (EMR + Clustering)

Provides:
  • Sidebar — "Process Documents" to run EMR ingestion + clustering pipeline.
  • Sidebar — "Text Clustering" section for cluster preview & download.
  • Sidebar — Executive Summary generator with model selection + download.
  • Main area — Chat interface with streaming LLM responses.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# Ensure the project root is on sys.path
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ===================================================================
# Page Configuration
# ===================================================================
st.set_page_config(
    page_title="RAG Fault Analyzer",
    page_icon="🔧",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ===================================================================
# Custom Styling
# ===================================================================
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@600;700&family=Inter:wght@400;500&display=swap');
    .block-container { padding-top: 2rem; }
    [data-testid="stSidebar"] { background-color: #0e1117; }
    .stChatMessage { border-radius: 12px; }
    h1, h2, h3 { font-family: 'Poppins', sans-serif !important; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ===================================================================
# Session State Initialization
# ===================================================================
for key, default in {
    "messages": [],
    "rag_chain": None,
    "pdf_bytes": None,
    "pdf_family": None,
    "html_content": None,
    "clustering_result": None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default


# ===================================================================
# Sidebar
# ===================================================================
with st.sidebar:
    st.title("⚙️ Pengaturan")
    st.markdown("---")

    # ── Section 1: Document Processing ──
    st.subheader("📄 Proses Dokumen")
    st.caption(
        "Letakkan file **Dashboard EMR.xlsx** di folder **`data_sumber/`**, "
        "lalu klik tombol di bawah untuk menjalankan pipeline:\n\n"
        "1. Load EMR data\n"
        "2. **Text Clustering** (UMAP + HDBSCAN + LLM labeling)\n"
        "3. Transform & embed ke ChromaDB"
    )

    if st.button("🚀 Process Documents", use_container_width=True):
        with st.spinner("Memproses EMR data + clustering… Ini mungkin memakan waktu."):
            try:
                from src.ingestion import process_documents

                result = process_documents()
                st.success(result)
                st.session_state.rag_chain = None  # Reset cached chain

                # Load clustering result for preview
                summary_path = _PROJECT_ROOT / "output" / "cluster_summary.json"
                if summary_path.exists():
                    with open(summary_path, "r", encoding="utf-8") as f:
                        st.session_state.clustering_result = json.load(f)

            except RuntimeError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"❌ Error:\n\n{exc}")

    st.markdown("---")

    # ── Section 2: Clustering Preview ──
    st.subheader("🔬 Text Clustering")

    # Try to load existing results
    summary_path = _PROJECT_ROOT / "output" / "cluster_summary.json"
    if st.session_state.clustering_result is None and summary_path.exists():
        with open(summary_path, "r", encoding="utf-8") as f:
            st.session_state.clustering_result = json.load(f)

    if st.session_state.clustering_result:
        clusters = st.session_state.clustering_result
        st.info(f"**{len(clusters)}** cluster terdeteksi")

        # Show top 10 clusters
        for i, c in enumerate(clusters[:10]):
            pct = c.get("percentage", 0)
            st.caption(
                f"**{i+1}. {c['label']}** — {c['size']} records ({pct}%)"
            )

        # Download clustered CSV
        csv_path = _PROJECT_ROOT / "output" / "clustered_emr.csv"
        if csv_path.exists():
            st.download_button(
                label="📥 Download Clustered CSV",
                data=csv_path.read_bytes(),
                file_name="clustered_emr.csv",
                mime="text/csv",
                use_container_width=True,
            )

        # Link to visualization
        viz_path = _PROJECT_ROOT / "output" / "cluster_visualization.html"
        if viz_path.exists():
            st.download_button(
                label="🗺️ Download Cluster Visualization",
                data=viz_path.read_bytes(),
                file_name="cluster_visualization.html",
                mime="text/html",
                use_container_width=True,
            )
    else:
        st.warning(
            "⚠️ Belum ada hasil clustering. "
            "Klik **Process Documents** terlebih dahulu."
        )

    st.markdown("---")

    # ── Section 3: Executive Summary ──
    st.subheader("📊 Executive Summary")
    st.caption(
        "Pilih model unit untuk menghasilkan Executive Summary. "
        "Laporan berisi analisis kategori masalah (AI clustering), "
        "distribusi site, dan rekomendasi AI."
    )

    try:
        from src.executive_summary import get_available_families

        families = get_available_families()
    except Exception:
        families = []

    if families:
        selected_family = st.selectbox(
            "Pilih Model Unit",
            options=families,
            index=families.index("PC200") if "PC200" in families else 0,
            help="Model diagregasi per family (misal: PC200 = PC200-10M0, dll.)",
        )

        col1, col2 = st.columns(2)
        with col1:
            use_llm = st.checkbox(
                "Gunakan AI",
                value=True,
                help="Aktifkan untuk rekomendasi AI",
            )

        if st.button(
            "📑 Generate Summary", use_container_width=True, type="primary"
        ):
            with st.spinner(
                f"Menghasilkan Executive Summary untuk **{selected_family}**..."
            ):
                try:
                    from src.executive_summary import generate_summary

                    pdf_bytes, html_content = generate_summary(
                        selected_family, use_llm=use_llm
                    )
                    st.session_state.pdf_bytes = pdf_bytes
                    st.session_state.html_content = html_content
                    st.session_state.pdf_family = selected_family
                    st.success(
                        f"✅ Executive Summary untuk **{selected_family}** berhasil!"
                    )
                except Exception as exc:
                    st.error(f"❌ Gagal:\n\n{exc}")

        if st.session_state.pdf_family:
            if st.session_state.html_content:
                st.download_button(
                    label="🌐 Download HTML Report",
                    data=st.session_state.html_content,
                    file_name=f"Executive_Summary_{st.session_state.pdf_family}.html",
                    mime="text/html",
                    use_container_width=True,
                )

            if st.session_state.pdf_bytes:
                st.download_button(
                    label="📄 Download PDF",
                    data=st.session_state.pdf_bytes,
                    file_name=f"Executive_Summary_{st.session_state.pdf_family}.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                )
    else:
        st.warning(
            "⚠️ Data belum tersedia. Pastikan **Dashboard EMR.xlsx** ada di "
            "`data_sumber/` dan klik **Process Documents**."
        )

    st.markdown("---")

    # ── Section 4: LLM Status ──
    st.subheader("🤖 Status LLM")
    import os
    from dotenv import load_dotenv

    load_dotenv(_PROJECT_ROOT / ".env")
    model_name = os.getenv("OLLAMA_MODEL", "llama3")
    embedding_name = os.getenv(
        "EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2"
    )
    st.info(f"Model LLM: **{model_name}** (Ollama)")
    st.info(f"Embedding: **{embedding_name}**")

    st.markdown("---")
    st.caption("RAG Fault Analyzer v3.0 — EMR + AI Clustering")


# ===================================================================
# Helper — lazily build the RAG chain
# ===================================================================
def _get_chain():
    if st.session_state.rag_chain is None:
        from src.chat_engine import get_chat_engine

        st.session_state.rag_chain = get_chat_engine()
    return st.session_state.rag_chain


# ===================================================================
# Main Layout — Chat Interface
# ===================================================================
st.title("🔧 RAG Fault Analyzer")
st.caption(
    "Chatbot analisis maintenance alat berat berbasis RAG — "
    "menggunakan data EMR (Electronic Maintenance Report) dengan "
    "AI text clustering untuk kategorisasi masalah otomatis."
)

# ── Preview Executive Summary ──
if st.session_state.get("html_content") and st.session_state.get("pdf_family"):
    with st.expander(
        f"📊 Preview: Executive Summary — {st.session_state.pdf_family}",
        expanded=False,
    ):
        st.components.v1.html(
            st.session_state.html_content, height=800, scrolling=True
        )
    st.markdown("---")

# ── Render existing chat history ──
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# ── Chat input ──
if user_input := st.chat_input("Ketik pertanyaan Anda di sini…"):
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        try:
            chain = _get_chain()
            from src.chat_engine import stream_answer

            response = st.write_stream(stream_answer(user_input, chain))
        except FileNotFoundError:
            response = (
                "⚠️ Database vektor belum tersedia. "
                "Silakan proses dokumen terlebih dahulu melalui sidebar."
            )
            st.warning(response)
        except Exception as exc:
            response = (
                f"❌ Terjadi kesalahan:\n\n"
                f"```\n{exc}\n```\n\n"
                "Pastikan Ollama berjalan dan model sudah di-pull."
            )
            st.error(response)

    st.session_state.messages.append(
        {"role": "assistant", "content": response}
    )
