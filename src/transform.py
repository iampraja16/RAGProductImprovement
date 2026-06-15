"""
transform.py — EMR Data Transformation Module

Converts EMR records into semantically rich natural language text
for embedding into the vector store.  Also generates aggregated
summary documents for model-level, cluster-level, and site-level
context.
"""

from __future__ import annotations

import logging
import sys
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from langchain_core.documents import Document

from .config import settings

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


# ===================================================================
# Helpers
# ===================================================================
def _safe(val: Any, default: str = "N/A") -> str:
    """Return stringified val, or default if NaN/empty."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return default
    s = str(val).strip()
    return s if s else default


def extract_model_family(model: str) -> str:
    """Extract base model family: 'PC200-10M0' -> 'PC200'."""
    if not model or model == "N/A":
        return model
    return str(model).split("-")[0]


# ===================================================================
# Loading Data
# ===================================================================
def load_emr_data(file_path: Optional[str] = None) -> pd.DataFrame:
    """
    Load EMR data from Excel or CSV and return as a DataFrame.
    """
    if not file_path:
        file_path = os.path.join(settings.data_dir, settings.emr_file_name)
        
    p = Path(file_path)

    if not p.exists():
        raise FileNotFoundError(
            f"File EMR tidak ditemukan: '{p}'.\n"
        )

    logger.info("Loading EMR data: %s", p.name)
    if p.suffix.lower() == '.csv':
        try:
            df = pd.read_csv(p, encoding='utf-8-sig')
        except UnicodeDecodeError:
            logger.info("  UTF-8 decoding failed — falling back to 'latin1' encoding")
            df = pd.read_csv(p, encoding='latin1')
    else:
        logger.info("  (sheet: %s)", settings.emr_sheet_name)
        df = pd.read_excel(p, sheet_name=settings.emr_sheet_name)
        
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
# Row -> Prompt-Ready Text
# ===================================================================
def transform_emr_row_to_text(row: pd.Series) -> str:
    """
    Convert a single EMR row into structured natural language.

    Example output
    --------------
    [EMR: U-00010957 | Model: D155A-6 | Site: JBY | Customer: PAMAPERSADA]
    Kategori Masalah: Final Drive System Failure
    Kejadian: RI Final Drive RH leak DZ1221
    Gejala: Oil leak
    Penyebab: Kerusakan floating seal pada FD RH ...
    Komponen: FINAL DRIVE RH SIDE | Sub: -
    Part: FLOATING SEAL (17M-27-00180)
    Tipe: Internal - Unschedule Breakdown
    Tanggal: 2025-04-13 -> Closed: 2025-04-17
    """
    emr = _safe(row.get("EMR Name"))
    model = _safe(row.get("Machine Model"))
    product = _safe(row.get("Machine Product"))
    serial = _safe(row.get("Serial Number"))
    site = _safe(row.get("Branch / Site"))
    account = _safe(row.get("Account: Account Name"))
    call_type = _safe(row.get("Sub Call Type"))
    pm_type = _safe(row.get("PMAct Type"))

    subjects = _safe(row.get("Subjects"))
    symptom = _safe(row.get("Symptom"))
    caused = _safe(row.get("Caused of Problem"))

    # Dynamic check for canonical fields from LLM-normalization pipeline
    symptom_clean = _safe(row.get("symptom_canonical"), "")
    cause_clean = _safe(row.get("cause_canonical"), "")
    action_clean = _safe(row.get("action_canonical"), "")

    tc = _safe(row.get("Techcare Component"))
    tc_sub = _safe(row.get("Techcare Sub Component"))
    part_no = _safe(row.get("Main Cause Part No"))
    part_desc = _safe(row.get("Part Description"))

    cluster_label = _safe(row.get("cluster_label"), "Belum dikategorisasi")

    created = _safe(row.get("Created Date"))
    closed = _safe(row.get("EMR Last Closed Date"))

    # Truncate long caused text for embedding quality
    caused_display = caused[:500] if len(caused) > 500 else caused

    lines = [
        f"[EMR: {emr} | Model: {model} ({product}) | SN: {serial} | "
        f"Site: {site} | Customer: {account}]",
        f"Kategori Masalah: {cluster_label}",
        f"Kejadian: {subjects}",
    ]

    if symptom_clean and symptom_clean != "N/A" and symptom_clean != "":
        lines.append(f"Gejala (Clean): {symptom_clean} (Mentah: {symptom})")
    else:
        lines.append(f"Gejala: {symptom}")

    if cause_clean and cause_clean != "N/A" and cause_clean != "":
        lines.append(f"Penyebab (Clean): {cause_clean} (Mentah: {caused_display})")
    else:
        lines.append(f"Penyebab: {caused_display}")

    if action_clean and action_clean != "N/A" and action_clean != "":
        lines.append(f"Tindakan Perbaikan (Clean): {action_clean}")

    if tc != "N/A" or tc_sub != "N/A":
        lines.append(f"Komponen: {tc} | Sub: {tc_sub}")
    if part_no != "N/A":
        lines.append(f"Part: {part_desc} ({part_no})")

    lines.append(f"Tipe: {call_type} | PM: {pm_type}")
    lines.append(f"Tanggal: {created} -> Closed: {closed}")

    return "\n".join(lines)


# ===================================================================
# Row -> Metadata
# ===================================================================
def build_emr_metadata(row: pd.Series) -> Dict[str, Any]:
    """Build metadata dict for ChromaDB from an EMR row."""
    meta: Dict[str, Any] = {
        "level": "emr_record",
        "source_type": "xlsx",
    }

    field_map = {
        "emr_name": "EMR Name",
        "machine_model": "Machine Model",
        "machine_product": "Machine Product",
        "serial_number": "Serial Number",
        "branch_site": "Branch / Site",
        "account": "Account: Account Name",
        "sub_call_type": "Sub Call Type",
        "cluster_label": "cluster_label",
        "cluster_id": "cluster_id",
        "techcare_component": "Techcare Component",
        "techcare_sub_component": "Techcare Sub Component",
    }

    for key, col in field_map.items():
        val = row.get(col)
        if val is not None and not (isinstance(val, float) and pd.isna(val)):
            s = str(val).strip()
            if s:
                meta[key] = s

    # Dates as strings
    for date_col in ("Created Date", "EMR Last Closed Date"):
        val = row.get(date_col)
        if val is not None and not (isinstance(val, float) and pd.isna(val)):
            meta[date_col.lower().replace(" ", "_")] = str(val)[:10]

    # Model family for easy filtering
    model = _safe(row.get("Machine Model"), "")
    if model:
        meta["model_family"] = extract_model_family(model)

    return meta


# ===================================================================
# Aggregation — Generate Summary Documents
# ===================================================================
def aggregate_model_summaries(df: pd.DataFrame) -> List[Document]:
    """
    Generate one summary Document per Machine Model.

    These give the LLM high-level context for model-specific queries.
    """
    docs: List[Document] = []

    for model, grp in df.groupby("Machine Model"):
        if pd.isna(model):
            continue

        family = extract_model_family(str(model))
        total = len(grp)
        top_clusters = (
            grp["cluster_label"]
            .value_counts()
            .head(5)
            .to_dict()
        )
        top_sites = grp["Branch / Site"].value_counts().head(5).to_dict()

        cluster_lines = "\n".join(
            f"  - {name}: {count} kejadian"
            for name, count in top_clusters.items()
        )
        site_lines = "\n".join(
            f"  - {site}: {count} kejadian"
            for site, count in top_sites.items()
        )

        text = (
            f"Model {model} (family {family}) memiliki total {total} EMR record.\n"
            f"Top 5 kategori masalah:\n{cluster_lines}\n"
            f"Top 5 site:\n{site_lines}"
        )

        docs.append(
            Document(
                page_content=text,
                metadata={
                    "level": "model_summary",
                    "machine_model": str(model),
                    "model_family": family,
                    "source_type": "aggregation",
                    "total_records": total,
                },
            )
        )

    logger.info("Generated %d model summary documents.", len(docs))
    return docs


def aggregate_cluster_summaries(df: pd.DataFrame) -> List[Document]:
    """
    Generate one summary Document per cluster label.
    """
    docs: List[Document] = []

    for label, grp in df.groupby("cluster_label"):
        if label == "Uncategorized":
            continue

        total = len(grp)
        top_models = grp["Machine Model"].value_counts().head(5).to_dict()
        top_sites = grp["Branch / Site"].value_counts().head(5).to_dict()

        model_lines = "\n".join(
            f"  - {m}: {c} kejadian" for m, c in top_models.items()
        )
        site_lines = "\n".join(
            f"  - {s}: {c} kejadian" for s, c in top_sites.items()
        )

        text = (
            f"Kategori masalah '{label}' memiliki total {total} kejadian EMR.\n"
            f"Model paling terdampak:\n{model_lines}\n"
            f"Site paling terdampak:\n{site_lines}"
        )

        docs.append(
            Document(
                page_content=text,
                metadata={
                    "level": "cluster_summary",
                    "cluster_label": str(label),
                    "source_type": "aggregation",
                    "total_records": total,
                },
            )
        )

    logger.info("Generated %d cluster summary documents.", len(docs))
    return docs


def aggregate_site_summaries(df: pd.DataFrame) -> List[Document]:
    """
    Generate one summary Document per Branch / Site.
    """
    docs: List[Document] = []

    for site, grp in df.groupby("Branch / Site"):
        if pd.isna(site):
            continue

        total = len(grp)
        top_models = grp["Machine Model"].value_counts().head(5).to_dict()
        top_clusters = grp["cluster_label"].value_counts().head(5).to_dict()

        model_lines = "\n".join(
            f"  - {m}: {c}" for m, c in top_models.items()
        )
        cluster_lines = "\n".join(
            f"  - {cl}: {c}" for cl, c in top_clusters.items()
        )

        text = (
            f"Site {site} memiliki total {total} EMR record.\n"
            f"Model terbanyak:\n{model_lines}\n"
            f"Masalah terbanyak:\n{cluster_lines}"
        )

        docs.append(
            Document(
                page_content=text,
                metadata={
                    "level": "site_summary",
                    "branch_site": str(site),
                    "source_type": "aggregation",
                    "total_records": total,
                },
            )
        )

    logger.info("Generated %d site summary documents.", len(docs))
    return docs


def get_model_summaries(df: pd.DataFrame) -> Dict[str, str]:
    """Helper to return dict of model name to its summary text."""
    docs = aggregate_model_summaries(df)
    return {doc.metadata["machine_model"]: doc.page_content for doc in docs}


def get_site_summaries(df: pd.DataFrame) -> Dict[str, str]:
    """Helper to return dict of site name to its summary text."""
    docs = aggregate_site_summaries(df)
    return {doc.metadata["branch_site"]: doc.page_content for doc in docs}
