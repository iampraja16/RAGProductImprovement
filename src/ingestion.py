"""
ingestion.py — EMR Hybrid Ingestion Pipeline

Handles:
  1. Loading Dashboard EMR.xlsx (or CSV/PDF fallback).
  2. Running the text clustering pipeline on free-text fields.
  3. Transforming each row into prompt-ready natural language.
  4. Generating aggregation summary documents.
  5. Embedding everything into ChromaDB.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.transform import (
    aggregate_cluster_summaries,
    aggregate_model_summaries,
    aggregate_site_summaries,
    build_emr_metadata,
    transform_emr_row_to_text,
)

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

DATA_DIR: str = os.getenv("DATA_DIR", "data_sumber")
CHROMA_DB_DIR: str = os.getenv("CHROMA_DB_DIR", "database_vektor")
EMBEDDING_MODEL: str = os.getenv(
    "EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2"
)
CHROMA_COLLECTION: str = os.getenv("CHROMA_COLLECTION", "product_docs")
EMR_FILE_NAME: str = os.getenv("EMR_FILE_NAME", "Dashboard EMR.xlsx")
EMR_SHEET_NAME: str = os.getenv("EMR_SHEET_NAME", "report1776669858353")
CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "500"))
CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "100"))


def _resolve_path(relative: str) -> Path:
    return (_PROJECT_ROOT / relative).resolve()


# ===================================================================
# 1. Load EMR Data
# ===================================================================
def load_emr_data(data_dir: Optional[str] = None) -> pd.DataFrame:
    """
    Load Dashboard EMR.xlsx and return the main sheet as a DataFrame.
    """
    source = _resolve_path(data_dir or DATA_DIR)
    xlsx_path = source / EMR_FILE_NAME

    if not xlsx_path.exists():
        raise FileNotFoundError(
            f"File EMR tidak ditemukan: '{xlsx_path}'.\n"
            f"Pastikan file '{EMR_FILE_NAME}' ada di folder '{source}'."
        )

    logger.info("Loading EMR data: %s (sheet: %s)", xlsx_path.name, EMR_SHEET_NAME)
    df = pd.read_excel(xlsx_path, sheet_name=EMR_SHEET_NAME)
    logger.info("  Loaded %d rows, %d columns.", len(df), len(df.columns))

    # Basic cleaning - strip spaces but keep duplicate names unique
    new_cols = []
    seen = {}
    for col in df.columns:
        clean_col = str(col).strip()
        if clean_col in seen:
            seen[clean_col] += 1
            new_cols.append(f"{clean_col}.{seen[clean_col]}")
        else:
            seen[clean_col] = 0
            new_cols.append(clean_col)
    df.columns = new_cols

    # Ensure key text columns exist
    for col in ("Subjects", "Symptom", "Caused of Problem"):
        if col not in df.columns:
            logger.warning("  Column '%s' not found — filling with empty.", col)
            df[col] = ""

    return df


# ===================================================================
# 2. Run Clustering
# ===================================================================
def run_clustering(
    df: pd.DataFrame,
    progress_callback=None,
):
    """
    Execute the hybrid two-stage clustering pipeline.

    Returns the updated DataFrame (with cluster columns) and the
    ClusteringResult object.
    """
    from src.text_clustering import run_clustering_pipeline

    logger.info("Starting text clustering pipeline on %d rows …", len(df))
    result = run_clustering_pipeline(df, progress_callback=progress_callback)

    logger.info(
        "Clustering complete: %d clusters, %d noise (%.1f%%)",
        result.n_clusters,
        result.noise_count,
        result.noise_pct,
    )
    return result


# ===================================================================
# 3. Transform Rows → Documents
# ===================================================================
def transform_emr_to_documents(df: pd.DataFrame) -> List[Document]:
    """
    Convert every EMR row into a prompt-ready LangChain Document.
    """
    documents: List[Document] = []

    for idx, row in df.iterrows():
        text = transform_emr_row_to_text(row)
        if not text.strip():
            continue

        metadata = build_emr_metadata(row)
        metadata["row_index"] = int(idx)
        metadata["filename"] = EMR_FILE_NAME

        documents.append(Document(page_content=text, metadata=metadata))

    logger.info("Transformed %d EMR rows → %d documents.", len(df), len(documents))
    return documents


# ===================================================================
# 4. PDF Processing (kept for backward compatibility)
# ===================================================================
def process_pdf(pdf_path: Path) -> List[Document]:
    """Parse a PDF file into LangChain Documents."""
    try:
        from unstructured.partition.pdf import partition_pdf
    except ImportError as exc:
        logger.error("unstructured[pdf] not installed.")
        raise ImportError(
            "Missing unstructured[pdf]. Install with: pip install 'unstructured[pdf]'"
        ) from exc

    logger.info("Parsing PDF: %s", pdf_path.name)

    elements = partition_pdf(
        filename=str(pdf_path), strategy="hi_res", infer_table_structure=True
    )

    documents: List[Document] = []
    for el in elements:
        category = getattr(el, "category", "Unknown")
        if category == "Table":
            text = (
                el.metadata.text_as_html
                if hasattr(el.metadata, "text_as_html") and el.metadata.text_as_html
                else str(el)
            )
        else:
            text = str(el)

        if not text.strip():
            continue

        documents.append(
            Document(
                page_content=text,
                metadata={
                    "source": str(pdf_path),
                    "filename": pdf_path.name,
                    "category": category,
                    "page_number": getattr(el.metadata, "page_number", None),
                    "source_type": "pdf",
                    "level": "document",
                },
            )
        )

    logger.info("Extracted %d elements from '%s'.", len(documents), pdf_path.name)
    return documents


def chunk_pdf_documents(documents: List[Document]) -> List[Document]:
    """Split PDF documents into smaller chunks (tables kept intact)."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        length_function=len,
        separators=["\n\n", "\n", ". ", " ", ""],
    )

    chunks: List[Document] = []
    for doc in documents:
        if doc.metadata.get("category") == "Table":
            chunks.append(doc)
        else:
            chunks.extend(splitter.split_documents([doc]))

    return chunks


# ===================================================================
# 5. Embed & Store
# ===================================================================
def embed_documents(chunks: List[Document]) -> None:
    """Embed document chunks and persist into ChromaDB."""
    from langchain_chroma import Chroma
    from langchain_huggingface import HuggingFaceEmbeddings

    chroma_dir = _resolve_path(CHROMA_DB_DIR)
    chroma_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Initializing embedding model: %s …", EMBEDDING_MODEL)
    embeddings = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )

    logger.info(
        "Storing %d chunks → ChromaDB '%s' (collection='%s') …",
        len(chunks),
        chroma_dir,
        CHROMA_COLLECTION,
    )

    Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=str(chroma_dir),
        collection_name=CHROMA_COLLECTION,
    )

    logger.info("✅ Embeddings stored successfully.")


# ===================================================================
# 6. Main Orchestrator
# ===================================================================
def process_documents(
    data_dir: Optional[str] = None,
    progress_callback=None,
) -> str:
    """
    End-to-end EMR ingestion pipeline:

    1. Load Dashboard EMR.xlsx
    2. Run text clustering (UMAP + HDBSCAN + LLM labeling)
    3. Transform rows → prompt-ready documents
    4. Generate aggregation summaries (model, cluster, site)
    5. Optionally process any PDFs in data_dir
    6. Embed everything into ChromaDB

    Returns a human-readable summary message.
    """
    # --- Step 1: Load EMR ---
    df = load_emr_data(data_dir)

    # --- Step 2: Clustering ---
    clustering_result = run_clustering(df, progress_callback=progress_callback)
    clustered_df = clustering_result.df

    # --- Step 3: Transform EMR rows → Documents ---
    emr_docs = transform_emr_to_documents(clustered_df)

    # --- Step 4: Aggregation summaries ---
    model_docs = aggregate_model_summaries(clustered_df)
    cluster_docs = aggregate_cluster_summaries(clustered_df)
    site_docs = aggregate_site_summaries(clustered_df)

    all_chunks: List[Document] = emr_docs + model_docs + cluster_docs + site_docs

    # --- Step 5: Process any PDFs in data_dir ---
    source = _resolve_path(data_dir or DATA_DIR)
    pdf_count = 0
    pdf_chunks = 0
    for pdf_path in sorted(source.glob("*.pdf")):
        try:
            docs = process_pdf(pdf_path)
            if docs:
                chunks = chunk_pdf_documents(docs)
                all_chunks.extend(chunks)
                pdf_chunks += len(chunks)
                pdf_count += 1
        except Exception as exc:
            logger.error("Failed to process PDF '%s': %s", pdf_path.name, exc)

    # --- Step 6: Embed ---
    if not all_chunks:
        raise RuntimeError("Tidak ada data berhasil diekstrak.")

    embed_documents(all_chunks)

    # --- Summary ---
    summary = (
        "✅ Ingestion selesai!\n"
        f"   • EMR records   : {len(emr_docs)} documents\n"
        f"   • Clusters found: {clustering_result.n_clusters} "
        f"(noise: {clustering_result.noise_pct:.1f}%)\n"
        f"   • Model summaries : {len(model_docs)}\n"
        f"   • Cluster summaries: {len(cluster_docs)}\n"
        f"   • Site summaries  : {len(site_docs)}\n"
        f"   • PDFs processed  : {pdf_count} ({pdf_chunks} chunks)\n"
        f"   • Total embedded  : {len(all_chunks)} chunks"
    )
    logger.info(summary)
    return summary


# Backward-compatible alias
def process_pdfs(data_dir: Optional[str] = None) -> str:
    return process_documents(data_dir)
