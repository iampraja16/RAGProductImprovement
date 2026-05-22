"""
text_clustering.py — Hybrid Two-Stage Text Clustering Pipeline

Stage 1: Embedding + UMAP + HDBSCAN  (algorithmic, scalable)
Stage 2: LLM-assisted cluster labeling (semantic, per-cluster)

Output: DataFrame with cluster_id, cluster_label columns + visualization.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from dotenv import load_dotenv

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
# Config
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

EMBEDDING_MODEL: str = os.getenv(
    "EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2"
)
HDBSCAN_MIN_CLUSTER_SIZE: int = int(os.getenv("HDBSCAN_MIN_CLUSTER_SIZE", "5"))
HDBSCAN_MIN_SAMPLES: int = int(os.getenv("HDBSCAN_MIN_SAMPLES", "3"))
UMAP_N_COMPONENTS: int = int(os.getenv("UMAP_N_COMPONENTS", "10"))
UMAP_N_NEIGHBORS: int = int(os.getenv("UMAP_N_NEIGHBORS", "15"))
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3")
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

_OUTPUT_DIR = _PROJECT_ROOT / "output"


# ===================================================================
# Result Container
# ===================================================================
@dataclass
class ClusteringResult:
    """Container for clustering pipeline output."""

    df: pd.DataFrame  # Original df + cluster columns
    n_clusters: int
    noise_count: int
    noise_pct: float
    cluster_labels: Dict[int, str]  # cluster_id -> label
    cluster_summary: List[Dict]
    visualization_path: str


# ===================================================================
# Stage 1 — Embedding & Algorithmic Clustering
# ===================================================================
def combine_text_fields(df: pd.DataFrame) -> pd.Series:
    """Combine Subjects + Symptom + Caused of Problem into one text."""
    subjects = df["Subjects"].fillna("")
    symptom = df["Symptom"].fillna("")
    caused = df["Caused of Problem"].fillna("")
    combined = subjects + " | " + symptom + " | " + caused
    # Clean up empty pipes
    combined = combined.str.replace(r"\s*\|\s*\|\s*", " | ", regex=True)
    combined = combined.str.strip(" |")
    return combined


def embed_texts(texts: List[str]) -> np.ndarray:
    """Embed texts using sentence-transformers (batch, CPU)."""
    from sentence_transformers import SentenceTransformer

    logger.info("Loading embedding model: %s …", EMBEDDING_MODEL)
    model = SentenceTransformer(EMBEDDING_MODEL)

    logger.info("Embedding %d texts …", len(texts))
    embeddings = model.encode(
        texts,
        batch_size=64,
        show_progress_bar=True,
        normalize_embeddings=True,
    )
    logger.info("Embeddings shape: %s", embeddings.shape)
    return embeddings


def reduce_dimensions(
    embeddings: np.ndarray, n_components: Optional[int] = None
) -> np.ndarray:
    """UMAP dimensionality reduction."""
    import umap

    n_comp = n_components or UMAP_N_COMPONENTS
    logger.info("UMAP: %d -> %d dims ...", embeddings.shape[1], n_comp)

    reducer = umap.UMAP(
        n_components=n_comp,
        n_neighbors=UMAP_N_NEIGHBORS,
        min_dist=0.1,
        metric="cosine",
        random_state=42,
    )
    return reducer.fit_transform(embeddings)


def cluster_embeddings(reduced: np.ndarray) -> np.ndarray:
    """HDBSCAN density-based clustering."""
    import hdbscan

    logger.info(
        "HDBSCAN (min_cluster_size=%d, min_samples=%d) …",
        HDBSCAN_MIN_CLUSTER_SIZE,
        HDBSCAN_MIN_SAMPLES,
    )
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=HDBSCAN_MIN_CLUSTER_SIZE,
        min_samples=HDBSCAN_MIN_SAMPLES,
        metric="euclidean",
        cluster_selection_method="eom",
    )
    labels = clusterer.fit_predict(reduced)

    n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
    n_noise = int((labels == -1).sum())
    logger.info(
        "Found %d clusters, %d noise (%.1f%%)",
        n_clusters,
        n_noise,
        n_noise / len(labels) * 100,
    )
    return labels


# ===================================================================
# Stage 2 — LLM-Assisted Labeling
# ===================================================================
def get_cluster_representatives(
    combined_texts: pd.Series,
    cluster_ids: np.ndarray,
    embeddings: np.ndarray,
    n_samples: int = 5,
) -> Dict[int, List[str]]:
    """Select representative texts per cluster (closest to centroid)."""
    representatives: Dict[int, List[str]] = {}

    for cid in sorted(set(cluster_ids)):
        if cid == -1:
            continue

        mask = cluster_ids == cid
        c_emb = embeddings[mask]
        c_texts = combined_texts.values[mask]

        centroid = c_emb.mean(axis=0)
        distances = np.linalg.norm(c_emb - centroid, axis=1)

        n_sel = min(n_samples, len(c_texts))
        closest = np.argsort(distances)[:n_sel]
        representatives[cid] = [c_texts[i] for i in closest]

    return representatives


def _label_one_cluster(cluster_id: int, reps: List[str]) -> Dict[str, Any]:
    """Ask Ollama to label a single cluster."""
    from langchain_ollama import ChatOllama
    from langchain_core.messages import HumanMessage

    llm = ChatOllama(
        model=OLLAMA_MODEL, base_url=OLLAMA_BASE_URL, temperature=0.0
    )

    samples = "\n".join(
        f"  {i + 1}. {t[:300]}" for i, t in enumerate(reps)
    )
    prompt = (
        "Kamu adalah expert maintenance alat berat. "
        f"Berikut {len(reps)} contoh masalah maintenance yang serupa "
        "(dikelompokkan oleh algoritma clustering):\n\n"
        f"{samples}\n\n"
        "Tugasmu:\n"
        "1. Analisis pola umum dari contoh-contoh di atas\n"
        "2. Berikan SATU label kategori singkat (2-4 kata, boleh bahasa "
        "Inggris) yang paling tepat menggambarkan kelompok masalah ini\n\n"
        "PENTING: Jawab HANYA dalam format JSON, tanpa teks lain:\n"
        '{"label": "Nama Kategori", "confidence": 0.85}'
    )

    try:
        resp = llm.invoke([HumanMessage(content=prompt)])
        content = resp.content.strip()
        
        # Robust JSON extraction: search for first '{' and last '}'
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            content = match.group(0)
        else:
            if "```" in content:
                content = re.sub(r"```(?:json)?\n?|\n?```", "", content).strip()

        result = json.loads(content)
        logger.info(
            "Cluster %d -> %s (%.2f)",
            cluster_id,
            result.get("label"),
            result.get("confidence", 0),
        )
        return result
    except Exception as exc:
        logger.warning("LLM label failed cluster %d: %s. Response was: %s", cluster_id, exc, resp.content if 'resp' in locals() else 'N/A')
        return {"label": f"Cluster_{cluster_id}", "confidence": 0.0}


def label_all_clusters(
    representatives: Dict[int, List[str]],
    progress_callback: Optional[Callable] = None,
) -> Dict[int, Dict[str, Any]]:
    """Label every cluster via LLM. Returns {cluster_id: {label, confidence}}."""
    results: Dict[int, Dict[str, Any]] = {}
    total = len(representatives)

    for i, (cid, reps) in enumerate(representatives.items()):
        logger.info("Labeling %d/%d (cluster %d) …", i + 1, total, cid)
        results[cid] = _label_one_cluster(cid, reps)
        if progress_callback:
            progress_callback(i + 1, total)

    return results


# ===================================================================
# Visualization
# ===================================================================
def generate_visualization(
    embeddings: np.ndarray,
    cluster_ids: np.ndarray,
    labels_map: Dict[int, Dict[str, Any]],
    combined_texts: pd.Series,
) -> str:
    """Interactive UMAP 2D scatter plot -> HTML file."""
    import plotly.express as px

    logger.info("Computing 2D UMAP for visualization …")
    reduced_2d = reduce_dimensions(embeddings, n_components=2)

    viz_df = pd.DataFrame(
        {
            "UMAP_1": reduced_2d[:, 0],
            "UMAP_2": reduced_2d[:, 1],
            "cluster_id": cluster_ids,
            "preview": combined_texts.str[:100].values,
        }
    )
    viz_df["label"] = viz_df["cluster_id"].map(
        {cid: info["label"] for cid, info in labels_map.items()}
    ).fillna("Noise / Uncategorized")

    fig = px.scatter(
        viz_df,
        x="UMAP_1",
        y="UMAP_2",
        color="label",
        hover_data=["preview", "cluster_id"],
        title="UMAP Cluster Visualization — EMR Text Clustering",
        template="plotly_white",
        width=1200,
        height=800,
    )
    fig.update_traces(marker=dict(size=3, opacity=0.6))
    fig.update_layout(legend=dict(font=dict(size=9)))

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = _OUTPUT_DIR / "cluster_visualization.html"
    fig.write_html(str(out))
    logger.info("Visualization -> %s", out)
    return str(out)


# ===================================================================
# Cluster Summary Builder
# ===================================================================
def build_cluster_summary(
    df: pd.DataFrame,
    cluster_ids: np.ndarray,
    labels_map: Dict[int, Dict[str, Any]],
) -> List[Dict]:
    """Per-cluster metadata summary."""
    summaries = []
    total_rows = len(df)

    for cid in sorted(set(cluster_ids)):
        if cid == -1:
            continue

        mask = cluster_ids == cid
        cdf = df[mask]
        info = labels_map.get(cid, {"label": f"Cluster_{cid}", "confidence": 0})

        top_models = cdf["Machine Model"].value_counts().head(5).to_dict()
        top_sites = cdf["Branch / Site"].value_counts().head(5).to_dict()

        tc = cdf["Techcare Component"].str.strip()
        top_tc = tc[tc != ""].value_counts().head(5).to_dict()

        summaries.append(
            {
                "cluster_id": int(cid),
                "label": info["label"],
                "confidence": info.get("confidence", 0),
                "size": int(mask.sum()),
                "percentage": round(mask.sum() / total_rows * 100, 2),
                "top_models": top_models,
                "top_sites": top_sites,
                "top_techcare": top_tc,
            }
        )

    return sorted(summaries, key=lambda x: x["size"], reverse=True)


# ===================================================================
# Main Pipeline
# ===================================================================
def run_clustering_pipeline(
    df: pd.DataFrame,
    progress_callback: Optional[Callable] = None,
) -> ClusteringResult:
    """
    Execute the full hybrid two-stage clustering pipeline.

    Parameters
    ----------
    df : DataFrame
        Raw EMR data with columns Subjects, Symptom, Caused of Problem.
    progress_callback : callable, optional
        fn(step: int, total: int) for progress reporting.

    Returns
    -------
    ClusteringResult
    """

    def _cb(step, total, msg=""):
        if progress_callback:
            progress_callback(step, total)

    # --- Stage 1 ---
    _cb(1, 6)
    combined = combine_text_fields(df)
    texts = combined.tolist()

    _cb(2, 6)
    embeddings = embed_texts(texts)

    _cb(3, 6)
    reduced = reduce_dimensions(embeddings)

    _cb(4, 6)
    cluster_ids = cluster_embeddings(reduced)

    # --- Stage 2 ---
    representatives = get_cluster_representatives(
        combined, cluster_ids, embeddings
    )

    _cb(5, 6)
    labels_map = label_all_clusters(representatives, progress_callback=None)

    # --- Apply to DataFrame ---
    df = df.copy()
    df["cluster_id"] = cluster_ids
    df["cluster_label"] = pd.Series(cluster_ids).map(
        {cid: info["label"] for cid, info in labels_map.items()}
    ).fillna("Uncategorized").values
    df["cluster_confidence"] = pd.Series(cluster_ids).map(
        {cid: info.get("confidence", 0) for cid, info in labels_map.items()}
    ).fillna(0.0).values

    # --- Summary ---
    summary = build_cluster_summary(df, cluster_ids, labels_map)

    # --- Save CSV ---
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = _OUTPUT_DIR / "clustered_emr.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    logger.info("Clustered CSV -> %s", csv_path)

    # Save summary JSON
    json_path = _OUTPUT_DIR / "cluster_summary.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    logger.info("Cluster summary -> %s", json_path)

    # --- Visualization ---
    _cb(6, 6)
    viz_path = generate_visualization(
        embeddings, cluster_ids, labels_map, combined
    )

    n_clusters = len(set(cluster_ids)) - (1 if -1 in cluster_ids else 0)
    n_noise = int((cluster_ids == -1).sum())

    return ClusteringResult(
        df=df,
        n_clusters=n_clusters,
        noise_count=n_noise,
        noise_pct=round(n_noise / len(df) * 100, 2),
        cluster_labels={cid: info["label"] for cid, info in labels_map.items()},
        cluster_summary=summary,
        visualization_path=viz_path,
    )
