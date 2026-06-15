# ===================================================================
# prompt.py — System Prompt & Token Utilities (FASE 3: Compressed)
# ===================================================================

# BEFORE: system_prompt was ~600 tokens (verbose, redundant with tool schemas)
# AFTER:  ≤200 tokens — compact, role-focused, format-only instructions

system_prompt = """Kamu AI analis fault alat berat (EMR data). Tools:
- ask_emr_graph: solusi/perbaikan/rekomendasi dari gejala
- ask_emr_knowledge: penyebab/gejala/deskripsi kerusakan
- ask_emr_database: jumlah/total/tren/ranking data
- generate_executive_summary: buat laporan per model family

Aturan:
1. Solusi/perbaikan → ask_emr_graph
2. Penyebab/gejala → ask_emr_knowledge
3. Jumlah/ranking → ask_emr_database
4. Laporan → generate_executive_summary

Aturan Numerik & SQL:
- Jika data hasil SQL query disajikan, kamu WAJIB menggunakan angka yang tertera di dalam tabel secara persis. Dilarang keras mengarang, memodifikasi, atau membulatkan angka tersebut.
- Jika data tabel hasil query memiliki lebih dari 10 baris, dilarang menulis ulang baris tersebut satu per satu di chat. Cukup tulis kesimpulan tren globalnya dan instruksikan user untuk merujuk pada tabel interaktif yang ditampilkan di bawah chat.

Jawab Bahasa Indonesia, singkat, markdown. Beri insight. Jangan mengarang data."""

# ===================================================================
# Token Estimation & Truncation (FASE 3)
# ===================================================================

# Conservative estimate: ~1 token per 3.5 chars for multilingual content
_CHARS_PER_TOKEN = 3.5
MAX_CONTEXT_TOKENS = 1800  # For num_ctx=2048, leave room for output
WARN_TOKENS = 1600
HARD_TRUNCATE_TOKENS = 1900  # Absolute max before truncation


def estimate_tokens(text: str) -> int:
    """Estimate token count using character-based heuristic."""
    return int(len(text) / _CHARS_PER_TOKEN)


def truncate_to_tokens(text: str, max_tokens: int = HARD_TRUNCATE_TOKENS) -> str:
    """Hard-truncate text to approximately max_tokens."""
    max_chars = int(max_tokens * _CHARS_PER_TOKEN)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[truncated]"


def format_compact_context(graph_text: str, vector_chunks: list = None, max_tokens: int = MAX_CONTEXT_TOKENS) -> str:
    """
    Assemble context from graph + vector results into compact format.
    Enforces max_tokens budget.

    PRD REQ-08: Total context ≤ 1800 tokens.
    """
    parts = []
    budget_chars = int(max_tokens * _CHARS_PER_TOKEN)

    # Graph context gets priority
    if graph_text:
        parts.append("CONTEXT (Graph):\n" + graph_text)

    # Add vector chunks if budget allows
    if vector_chunks:
        parts.append("CONTEXT (Docs):")
        for i, chunk in enumerate(vector_chunks[:3]):
            parts.append(f"- Doc{i+1}: {chunk[:300]}")

    assembled = "\n".join(parts)

    # Enforce budget
    if len(assembled) > budget_chars:
        assembled = assembled[:budget_chars] + "\n...[truncated]"

    return assembled
