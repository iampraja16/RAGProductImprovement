"""
chat_engine.py — RAG Chat Engine (EMR-Aware)

Handles:
  1. Loading the persistent ChromaDB vector store.
  2. Connecting to a local Ollama LLM.
  3. Metadata-aware retrieval that prioritizes EMR record data.
  4. Building a LangChain LCEL chain for response generation.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Generator, List, Optional

from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda, RunnablePassthrough

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(
    logging.Formatter("[%(asctime)s] %(levelname)s — %(name)s — %(message)s")
)
logger.addHandler(_handler)

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3")
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
CHROMA_DB_DIR: str = os.getenv("CHROMA_DB_DIR", "database_vektor")
EMBEDDING_MODEL: str = os.getenv(
    "EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2"
)
CHROMA_COLLECTION: str = os.getenv("CHROMA_COLLECTION", "product_docs")
RETRIEVER_K: int = int(os.getenv("RETRIEVER_K", "15"))


def _resolve_path(relative: str) -> Path:
    return (_PROJECT_ROOT / relative).resolve()


# ===================================================================
# System Prompt — Expert Machine Fault Analysis (EMR-Aware)
# ===================================================================
SYSTEM_PROMPT: str = """\
Kamu adalah **AI Expert Analisis Fault Alat Berat** berbasis data EMR (Electronic Maintenance Report). Tugasmu adalah menganalisis data maintenance dan memberikan insight yang mendalam.

**SUMBER DATA:**
Data berasal dari Dashboard EMR yang mencakup:
- **Subjects**: Deskripsi singkat kejadian
- **Symptom**: Gejala yang diamati
- **Caused of Problem**: Analisis penyebab (root cause)
- **Cluster Label**: Kategori masalah hasil AI clustering (misal: "Engine Overheating", "Hydraulic System Leak", dll.)
- **Machine Model, Serial Number, Branch/Site, Account, Techcare Component**

**ATURAN UTAMA:**
1. **Bahasa**: Selalu gunakan Bahasa Indonesia yang profesional dan lugas.
2. **Format**: 
   - Gunakan **Tabel Markdown** untuk daftar, perbandingan, atau data statistik.
   - Gunakan bullet points untuk detail pendukung.
3. **Insight**: Jangan hanya membaca data. Berikan analisis "Next Step" (apa yang harus dilakukan tim maintenance).
4. **Ranking & Statistik**: Selalu hitung dan tentukan urutan ranking sendiri berdasarkan data di konteks. Prioritaskan FREKUENSI ABSOLUT.
5. **Cluster/Kategori**: Gunakan label cluster (Kategori Masalah) yang ada di data untuk mengelompokkan masalah. Ini adalah kategori yang dihasilkan oleh AI clustering dari teks bebas EMR.
6. **Fokus Model & Exact Match**: Jika pengguna bertanya tentang model spesifik (misal: PC200), fokuslah HANYA pada data model tersebut. Bedakan PC200 dari PC2000.
7. **Kejujuran Data**: Jika data spesifik tidak ditemukan, sampaikan dengan sopan.
8. **Gaya Bahasa**: Jadilah asisten yang proaktif dan informatif. Hindari gaya robotik.
9. **Sumber**: Jangan menyebutkan nama file sumber secara eksplisit.

**KONTEKS DATA:**
{context}

**PERTANYAAN PENGGUNA:**
{question}
"""

_PROMPT = ChatPromptTemplate.from_template(SYSTEM_PROMPT)


# ===================================================================
# Components
# ===================================================================
def _get_vectorstore() -> Any:
    from langchain_chroma import Chroma
    from langchain_huggingface import HuggingFaceEmbeddings

    chroma_dir = _resolve_path(CHROMA_DB_DIR)
    if not chroma_dir.exists():
        raise FileNotFoundError(
            f"Database vektor tidak ditemukan di '{chroma_dir}'."
        )

    embeddings = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )
    return Chroma(
        persist_directory=str(chroma_dir),
        embedding_function=embeddings,
        collection_name=CHROMA_COLLECTION,
    )


def _get_llm() -> Any:
    from langchain_ollama import ChatOllama

    return ChatOllama(
        model=OLLAMA_MODEL,
        base_url=OLLAMA_BASE_URL,
        temperature=0.0,
    )


# ===================================================================
# Retrieval & Formatting
# ===================================================================
def _format_docs(docs: List[Document]) -> str:
    if not docs:
        return "TIDAK ADA DATA TERSEDIA."

    formatted = []
    for i, doc in enumerate(docs):
        meta = doc.metadata
        level = meta.get("level", "general")
        model = meta.get("machine_model", "N/A")
        cluster = meta.get("cluster_label", "N/A")
        site = meta.get("branch_site", "N/A")

        ctx_header = (
            f"[Data {i + 1} | Level: {level} | "
            f"Model: {model} | Kategori: {cluster} | Site: {site}]"
        )
        formatted.append(f"{ctx_header}\n{doc.page_content}")

    return "\n\n---\n\n".join(formatted)


def _get_retriever(vectorstore: Any) -> Any:
    # Priority: individual EMR records first, then summaries
    _LEVEL_PRIORITY = {
        "emr_record": 1,
        "model_summary": 2,
        "cluster_summary": 3,
        "site_summary": 4,
        "document": 5,
    }

    def _retrieve(query: str) -> List[Document]:
        results = vectorstore.similarity_search(query, k=RETRIEVER_K)

        results.sort(
            key=lambda d: _LEVEL_PRIORITY.get(
                d.metadata.get("level", ""), 99
            )
        )

        logger.info(
            "Retrieved %d docs. Top levels: %s",
            len(results),
            [d.metadata.get("level") for d in results[:5]],
        )
        return results

    return RunnableLambda(_retrieve)


# ===================================================================
# Public API
# ===================================================================
def ask_question(query: str, engine: Optional[Any] = None) -> str:
    """Main entry point for answering questions."""
    if engine is None:
        vectorstore = _get_vectorstore()
        llm = _get_llm()
    else:
        vectorstore = engine.vectorstore
        llm = engine.llm

    retriever = _get_retriever(vectorstore)

    chain = (
        {"context": retriever | _format_docs, "question": RunnablePassthrough()}
        | _PROMPT
        | llm
        | StrOutputParser()
    )

    return chain.invoke(query)


def stream_answer(
    query: str, engine: Optional[Any] = None
) -> Generator[str, None, None]:
    """Streaming version for UI."""
    if engine is None:
        vectorstore = _get_vectorstore()
        llm = _get_llm()
    else:
        vectorstore = engine.vectorstore
        llm = engine.llm

    retriever = _get_retriever(vectorstore)

    chain = (
        {"context": retriever | _format_docs, "question": RunnablePassthrough()}
        | _PROMPT
        | llm
        | StrOutputParser()
    )

    for chunk in chain.stream(query):
        yield chunk


class ChatEngine:
    """Class wrapper for persistent objects."""

    def __init__(self) -> None:
        self.vectorstore = _get_vectorstore()
        self.llm = _get_llm()


def get_chat_engine() -> ChatEngine:
    return ChatEngine()
