"""
executive_summary.py — Executive Summary Generator (EMR-Based)

Reads directly from Dashboard EMR.xlsx + clustering results to generate
premium A4-ready HTML/PDF reports per model family.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
from jinja2 import Environment, FileSystemLoader

# ---------------------------------------------------------------------------
# Logger & Paths
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Environment
from src.config import settings

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _PROJECT_ROOT / settings.data_dir
_TEMPLATE_DIR = _PROJECT_ROOT / "templates"
_ASSETS_DIR = _PROJECT_ROOT / "assets"
_IMAGES_DIR = _ASSETS_DIR / "unit_images"
_OUTPUT_DIR = _PROJECT_ROOT / "output"

EMR_FILE_NAME: str = settings.emr_file_name
EMR_SHEET_NAME: str = settings.emr_sheet_name

# Design Tokens
_COLORS = ["#4361ee", "#3a86ff", "#4895ef", "#4cc9f0", "#72efdd",
           "#8338ec", "#ff006e", "#fb5607", "#ffbe0b", "#06d6a0"]
_ACCENT = "#e63946"
_BG_COLOR = "#ffffff"
_TEXT_COLOR = "#1a1a2e"
_GRID_COLOR = "#f0f0f0"


# ===================================================================
# Data Loading
# ===================================================================
def _load_emr_data() -> pd.DataFrame:
    """Load EMR data directly from PostgreSQL."""
    try:
        from sqlalchemy import create_engine
        engine = create_engine(settings.postgres_url)
        df = pd.read_sql("SELECT * FROM emr_records", engine)
        
        # Ensure column names map correctly to what the code expects
        # emr_records columns are lowercase with underscores (e.g., machine_model)
        # The legacy code expects title case with spaces (e.g., Machine Model)
        rename_map = {
            "machine_model": "Machine Model",
            "branch_site": "Branch / Site",
            "account_account_name": "Account: Account Name",
            "created_date": "Created Date",
            "emr_last_closed_date": "EMR Last Closed Date",
            "techcare_component": "Techcare Component"
        }
        df.rename(columns=rename_map, inplace=True)
        
        for col in ("Created Date", "EMR Last Closed Date"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
                
        return df
    except Exception as e:
        logger.error("Failed to load from PostgreSQL: %s", e)
        return pd.DataFrame()


def extract_model_family(model_type: str) -> str:
    """Extract base family: 'PC200-10M0' -> 'PC200'."""
    if not model_type or (isinstance(model_type, float) and pd.isna(model_type)):
        return "Unknown"
    return str(model_type).split("-")[0]


def get_available_families() -> List[str]:
    """Return sorted list of unique model families in the EMR data."""
    try:
        df = _load_emr_data()
        families = (
            df["Machine Model"]
            .dropna()
            .apply(extract_model_family)
            .unique()
            .tolist()
        )
        return sorted(families)
    except Exception:
        return []


def _get_subtypes_for_family(df: pd.DataFrame, family: str) -> List[str]:
    """Get all model variants for a family."""
    mask = df["Machine Model"].dropna().apply(
        lambda x: extract_model_family(x) == family
    )
    return sorted(df.loc[mask, "Machine Model"].unique().tolist())


# ===================================================================
# Summary Data Extraction
# ===================================================================
def extract_summary_data(family: str) -> Dict[str, Any]:
    """
    Extract all summary data for a model family directly from EMR data.
    """
    df = _load_emr_data()

    subtypes = _get_subtypes_for_family(df, family)
    family_mask = df["Machine Model"].apply(
        lambda x: extract_model_family(str(x)) == family
        if pd.notna(x) else False
    )
    fdf = df[family_mask]

    data: Dict[str, Any] = {
        "family": family,
        "subtypes": subtypes,
    }

    # --- Total EMR records ---
    data["total_faults"] = len(fdf)

    # --- Rank among all families ---
    all_counts = (
        df["Machine Model"]
        .dropna()
        .apply(extract_model_family)
        .value_counts()
    )
    rank_list = all_counts.index.tolist()
    data["rank_position"] = (rank_list.index(family) + 1) if family in rank_list else 0
    data["total_models"] = len(rank_list)

    # --- Top fault categories (from clustering) ---
    has_clusters = "graph_community_summary" in fdf.columns
    if has_clusters:
        cluster_dist = (
            fdf["graph_community_summary"]
            .value_counts()
            .head(5)
            .reset_index()
        )
        cluster_dist.columns = ["fault_name", "frequency"]
        total = len(fdf) or 1
        cluster_dist["percentage"] = round(
            cluster_dist["frequency"] / total * 100, 2
        )
        data["top_faults"] = cluster_dist.to_dict("records")
        data["unique_fault_count"] = fdf["graph_community_summary"].nunique()
    else:
        # Fallback: use Techcare Component
        tc = fdf["Techcare Component"].str.strip()
        tc_dist = (
            tc[tc != ""]
            .value_counts()
            .head(5)
            .reset_index()
        )
        tc_dist.columns = ["fault_name", "frequency"]
        total = len(fdf) or 1
        tc_dist["percentage"] = round(tc_dist["frequency"] / total * 100, 2)
        data["top_faults"] = tc_dist.to_dict("records")
        data["unique_fault_count"] = tc[tc != ""].nunique()

    # --- Pareto cumulative ---
    if data["top_faults"]:
        cum = 0.0
        pareto = []
        for f in data["top_faults"]:
            cum += f["percentage"]
            pareto.append(round(cum, 2))
        data["pareto_cumulative"] = pareto

    # --- Top 3 concentration ---
    top3 = data["top_faults"][:3]
    data["top3_concentration"] = round(
        sum(f["percentage"] for f in top3), 2
    )
    data["top3_pattern"] = {}
    for i in range(3):
        if i < len(top3):
            data["top3_pattern"][f"fault_{i+1}"] = top3[i]["fault_name"]
            data["top3_pattern"][f"pct_{i+1}"] = top3[i]["percentage"]
        else:
            data["top3_pattern"][f"fault_{i+1}"] = "N/A"
            data["top3_pattern"][f"pct_{i+1}"] = 0

    # --- Top sites ---
    site_agg = (
        fdf.groupby(["Branch / Site", "Account: Account Name"])
        .size()
        .sort_values(ascending=False)
        .reset_index(name="total_fault_count")
        .head(5)
    )
    site_agg.rename(
        columns={"Branch / Site": "plan", "Account: Account Name": "account"},
        inplace=True,
    )
    data["top_sites"] = site_agg.to_dict("records")
    data["active_sites"] = fdf["Branch / Site"].nunique()

    # --- Date range ---
    if "Created Date" in fdf.columns:
        dates = fdf["Created Date"].dropna()
        if len(dates):
            data["date_from"] = str(dates.min())[:10]
            data["date_to"] = str(dates.max())[:10]

    # --- AI Recommendation (via LLM) ---
    data["recommendation"] = _generate_recommendation(data)

    return data


def _generate_recommendation(data: Dict[str, Any]) -> str:
    """Generate AI recommendation using cloud LLM."""
    try:
        from src.services.providers import get_llm
        from langchain_core.messages import HumanMessage

        llm = get_llm(temperature=0.0)
        top_faults_text = "\n".join(
            f"  - {f['fault_name']}: {f['frequency']} kejadian ({f['percentage']}%)"
            for f in data.get("top_faults", [])[:5]
        )
        prompt = (
            f"Berdasarkan data EMR model {data['family']}:\n"
            f"Total: {data.get('total_faults', 0)} record\n"
            f"Top masalah:\n{top_faults_text}\n\n"
            "Berikan 3-5 rekomendasi aksi maintenance yang spesifik dan actionable "
            "dalam format bullet points. Gunakan Bahasa Indonesia."
        )
        resp = llm.invoke([HumanMessage(content=prompt)])
        return resp.content.strip()
    except Exception as exc:
        logger.warning("LLM recommendation failed: %s", exc)
        return (
            "Rekomendasi: Lakukan pengecekan rutin pada komponen kritikal "
            "berdasarkan kategori masalah teratas."
        )


# ===================================================================
# Visualizations
# ===================================================================
def _fig_to_base64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(
        buf, format="png", dpi=180, bbox_inches="tight", facecolor=_BG_COLOR
    )
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def generate_pareto_chart(data: Dict) -> str:
    faults = data.get("top_faults", [])
    if not faults:
        return ""

    names = [
        (f["fault_name"][:25] + "…") if len(f["fault_name"]) > 25 else f["fault_name"]
        for f in faults[:5]
    ]
    freqs = [f["frequency"] for f in faults[:5]]
    cum = data.get("pareto_cumulative", [])

    fig, ax1 = plt.subplots(figsize=(8, 4.5))
    fig.patch.set_facecolor(_BG_COLOR)
    ax1.set_facecolor(_BG_COLOR)

    ax1.bar(
        range(len(names)), freqs,
        color=_COLORS[: len(names)], zorder=3, width=0.6, alpha=0.9,
    )
    ax1.set_xticks(range(len(names)))
    ax1.set_xticklabels(names, rotation=25, ha="right", fontsize=9, color=_TEXT_COLOR)
    ax1.set_ylabel("Frekuensi Kejadian", fontsize=10, fontweight="bold", color=_TEXT_COLOR)
    ax1.grid(axis="y", color=_GRID_COLOR, linestyle="--", linewidth=0.7, zorder=0)

    ax2 = ax1.twinx()
    if cum:
        ax2.plot(
            range(len(cum)), cum,
            color=_ACCENT, marker="o", markersize=6, linewidth=2.5, zorder=4,
        )
        ax2.set_ylabel("Kontribusi Kumulatif (%)", fontsize=10, fontweight="bold", color=_ACCENT)
        ax2.set_ylim(0, 105)
        for i, txt in enumerate(cum):
            ax2.annotate(
                f"{txt}%", (i, cum[i]),
                textcoords="offset points", xytext=(0, 10),
                ha="center", fontsize=9, fontweight="bold", color=_ACCENT,
            )

    for ax in [ax1, ax2]:
        ax.spines["top"].set_visible(False)
        ax.spines["left"].set_visible(False)
        ax.spines["right"].set_visible(False)

    fig.tight_layout()
    return _fig_to_base64(fig)


def generate_site_chart(data: Dict) -> str:
    sites = data.get("top_sites", [])
    if not sites:
        return ""

    labels = [f"{s['plan']} — {str(s['account'])[:18]}" for s in sites[:5]][::-1]
    values = [s["total_fault_count"] for s in sites[:5]][::-1]

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.barh(
        range(len(labels)), values,
        color=_COLORS[: len(values)], height=0.6, zorder=3,
    )
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=9, color=_TEXT_COLOR)
    ax.set_xlabel("Total EMR", fontsize=10, fontweight="bold", color=_TEXT_COLOR)
    ax.set_title(
        "Distribusi EMR per Site", fontsize=12, fontweight="bold",
        color=_TEXT_COLOR, pad=15,
    )
    ax.grid(axis="x", color=_GRID_COLOR, linestyle="--", linewidth=0.7, zorder=0)

    for i, v in enumerate(values):
        ax.text(v + 10, i, f"{v:,}", va="center", fontsize=9, fontweight="bold", color=_TEXT_COLOR)

    for s in ["top", "right", "left"]:
        ax.spines[s].set_visible(False)

    fig.tight_layout()
    return _fig_to_base64(fig)


def generate_donut_chart(data: Dict) -> str:
    pct = data.get("top3_concentration", 0)

    fig, ax = plt.subplots(figsize=(4, 4))
    wedges, _ = ax.pie(
        [pct, 100 - pct],
        colors=[_COLORS[0], "#f5f5f5"],
        startangle=90,
        wedgeprops={"width": 0.45, "edgecolor": "white"},
    )
    ax.text(0, 0.05, f"{pct}%", ha="center", va="center",
            fontsize=28, fontweight="bold", color=_COLORS[0])
    ax.text(0, -0.2, "Top 3 Concentration", ha="center", va="center",
            fontsize=10, color=_TEXT_COLOR)
    return _fig_to_base64(fig)


# ===================================================================
# Final Pipeline
# ===================================================================
def generate_summary(
    family: str, use_llm: bool = True
) -> Tuple[bytes, str]:
    """
    Generate executive summary HTML + PDF for a model family.

    Returns (pdf_bytes, html_string).
    """
    data = extract_summary_data(family)

    # Ensure mandatory fields
    if "top3_pattern" not in data:
        data["top3_pattern"] = {
            "fault_1": "N/A", "pct_1": 0,
            "fault_2": "N/A", "pct_2": 0,
            "fault_3": "N/A", "pct_3": 0,
        }

    charts = {
        "pareto": generate_pareto_chart(data),
        "site": generate_site_chart(data),
        "donut": generate_donut_chart(data),
    }

    unit_img = ""
    for ext in (".png", ".jpg"):
        p = _IMAGES_DIR / f"{family}{ext}"
        if p.exists():
            unit_img = base64.b64encode(p.read_bytes()).decode("utf-8")
            break

    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)))
    template = env.get_template("executive_summary.html")

    template_data = data.copy()
    template_data.update(
        {
            "subtypes": ", ".join(data.get("subtypes", [])),
            "total_faults": f"{int(data.get('total_faults', 0)):,}",
            "pareto_chart": charts["pareto"],
            "site_chart": charts["site"],
            "donut_chart": charts["donut"],
            "unit_image": unit_img,
            "generated_at": datetime.now().strftime("%d %B %Y, %H:%M WIB"),
        }
    )

    html_content = template.render(**template_data)

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (_OUTPUT_DIR / f"Report_{family}.html").write_text(
        html_content, encoding="utf-8"
    )

    try:
        from xhtml2pdf import pisa
        buf = io.BytesIO()
        pisa.CreatePDF(io.StringIO(html_content), dest=buf)
        pdf_bytes = buf.getvalue()
    except Exception as exc:
        logger.warning("PDF generation failed: %s", exc)
        pdf_bytes = b""

    return pdf_bytes, html_content
