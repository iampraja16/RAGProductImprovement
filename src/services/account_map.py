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


def resolve_account_mentions(query: str) -> Tuple[str, Optional[str]]:
    """Detect account/customer names in query and return (query, hint_string_or_None).

    Matching strategy (all case-insensitive):
      1. Full/partial account name appears in query
         "PAMAPERSADA NUSANTARA" → match
      2. Query token appears as substring of any account name
         "PAMA" → matches "PAMAPERSADA NUSANTARA"
         "ADARO" → matches "ADARO INDONESIA", "ADARO LOGISTICS"
      3. Multiple tokens from query match multiple accounts → OR hint

    Returns:
      (original_query, None) if no account detected.
      (original_query, "account_account_name = 'X' OR ...") if found.
    """
    _load_account_names()
    if not _ACCOUNT_NAMES:
        return query, None

    query_lower = query.lower()
    query_tokens = {t for t in query_lower.split() if len(t) >= 3}
    found: List[str] = []

    # Strategy 1: account name (or part of it) appears in query
    for acct_name, acct_lower in zip(_ACCOUNT_NAMES, _ACCOUNT_NAMES_LOWER):
        if acct_lower in query_lower:
            if acct_name not in found:
                found.append(acct_name)

    # Strategy 2: query token matches part of an account name
    for token in query_tokens:
        for acct_name, acct_lower in zip(_ACCOUNT_NAMES, _ACCOUNT_NAMES_LOWER):
            if token in acct_lower:
                if acct_name not in found:
                    found.append(acct_name)

    if not found:
        return query, None

    hints = [f"account_account_name = '{name}'" for name in found]
    hint_str = " OR ".join(hints)
    return query, hint_str
