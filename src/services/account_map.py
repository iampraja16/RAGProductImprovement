"""Account/customer name resolution for EMR queries.

Dynamically loads all unique account_account_name values from PostgreSQL
at module load time and provides resolve_account_mentions() mirroring the
site_map.py pattern.

Instead of hardcoding 1,193 account names, we load them from the database
and match via substring containment (token in account_name). This handles
both full names ("PAMAPERSADA NUSANTARA") and abbreviations ("PAMA").
"""

import logging
from typing import Tuple, Optional, List

logger = logging.getLogger(__name__)

_ACCOUNT_NAMES: List[str] = []
_ACCOUNT_NAMES_LOWER: List[str] = []
_LOADED = False

_QUERY_SKIP_WORDS = frozenset({
    "site", "area", "unit", "plan", "plant", "lokasi", "branch",
    "cari", "data", "info", "list", "show", "find",
    "total", "count", "nomor", "angka",
    "satu", "dua", "tiga", "empat", "lima",
    "semua", "setiap", "per", "rata",
    "masalah", "problem", "error", "fault", "issue", "case",
    "first", "last", "top", "most",
    "account", "customer", "type", "code", "name",
    "engine", "parts", "service", "system",
    "yang", "di", "ke", "dan", "atau", "pada", "dengan", "untuk",
    "saya", "kamu", "ini", "itu", "ada", "tidak", "akan", "dapat",
    "the", "a", "an", "of", "in", "on", "to", "for", "with",
    "and", "or", "is", "are", "was", "were",
    "please", "tampilkan", "sebutkan", "berikan", "tolong", "mohon",
    "membahas", "mengenai", "tentang", "dimana", "bagaimana",
    "apakah", "adakah", "berapa", "banyak", "beberapa"
})

from src.services.site_map import SITE_MAP
for _site_name in SITE_MAP.keys():
    _QUERY_SKIP_WORDS = _QUERY_SKIP_WORDS.union(set(_site_name.lower().split()))

def _load_account_names():
    global _ACCOUNT_NAMES, _ACCOUNT_NAMES_LOWER, _LOADED
    if _LOADED:
        return
    try:
        from sqlalchemy import create_engine, text
        from src.config import settings
        engine = create_engine(settings.readonly_postgres_url)
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT DISTINCT account_account_name FROM emr_records WHERE account_account_name IS NOT NULL ORDER BY account_account_name")
            ).fetchall()
            _ACCOUNT_NAMES = [row[0] for row in rows]
            _ACCOUNT_NAMES_LOWER = [name.lower() for name in _ACCOUNT_NAMES]
        logger.info(f"Loaded {len(_ACCOUNT_NAMES)} unique account names")
    except Exception as e:
        logger.warning(f"Failed to load account names: {e}")
        _ACCOUNT_NAMES = []
        _ACCOUNT_NAMES_LOWER = []
    finally:
        _LOADED = True


_ACCOUNT_ALIASES = {
    "pama persada": "PAMAPERSADA NUSANTARA",
    "pamapersada": "PAMAPERSADA NUSANTARA",
    "pama indo": "PAMA INDO MINING",
    "kpc": "KALTIM PRIMA COAL",
    "adaro": "ADARO INDONESIA", # Default to Indonesia if just adaro is not enough, wait let's just do pama
}

def resolve_account_mentions(query: str) -> Tuple[str, Optional[str]]:
    """Detect account/customer names in query and return (query, hint_string_or_None)."""
    _load_account_names()
    if not _ACCOUNT_NAMES:
        return query, None

    query_lower = query.lower()
    found: List[str] = []

    # Check explicit aliases first
    for alias, actual in _ACCOUNT_ALIASES.items():
        if alias in query_lower:
            if actual in _ACCOUNT_NAMES and actual not in found:
                found.append(actual)
            query_lower = query_lower.replace(alias, "")

    query_tokens = {t for t in query_lower.split() if len(t) >= 3}

    # Strategy 1: account name (or part of it) appears in query
    for acct_name, acct_lower in zip(_ACCOUNT_NAMES, _ACCOUNT_NAMES_LOWER):
        if len(acct_lower) > 5 and acct_lower in query_lower:
            if acct_name not in found:
                found.append(acct_name)
            query_lower = query_lower.replace(acct_lower, "")
            # Recompute tokens after removing matched account name
            query_tokens = {t for t in query_lower.split() if len(t) >= 3}

    # Strategy 2: query token matches part of an account name
    # Skip common meta-words that should never trigger account matching
    for token in query_tokens:
        if token in _QUERY_SKIP_WORDS:
            continue
        for acct_name, acct_lower in zip(_ACCOUNT_NAMES, _ACCOUNT_NAMES_LOWER):
            if len(token) >= 5:
                if token in acct_lower:
                    if acct_name not in found:
                        found.append(acct_name)
            else:
                if any(word.startswith(token) for word in acct_lower.split()):
                    if acct_name not in found:
                        found.append(acct_name)

    if not found:
        return query, None

    hints = [f"account_account_name = '{name}'" for name in found]
    hint_str = " OR ".join(hints)
    return query, hint_str
