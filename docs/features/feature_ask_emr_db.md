# Dokumentasi Fitur: ask_emr_database

## Apa yang Dilakukan Fitur Ini?

Fitur `ask_emr_database` adalah **pintu utama untuk bertanya soal angka dan statistik** ke database EMR.

Gunakan fitur ini kalau kamu mau tanya hal-hal seperti:
- *"Berapa total kerusakan engine overheat?"*
- *"Top 5 komponen yang paling sering rusak"*
- *"Tren kerusakan per bulan di tahun 2025"*
- *"Masalah apa yang sering terjadi di site Jembayan?"*
- *"Berapa banyak EMR untuk model PC200?"*

Fitur ini kerjanya: **mengubah pertanyaan bahasa Indonesia → SQL → menjalankan ke PostgreSQL → ngasih jawaban dalam bentuk tabel.**

## Alur Kerja (Flowchart)

```mermaid
graph TD
    A[Kamu tanya: Berapa total hydraulic leak] --> B[EntityResolver]
    
    B --> C1{Ada nama site\natau account?}
    C1 -->|Ya| D[Skip EntityResolver!\nLangsung pakai site/account filter]
    C1 -->|Tidak| E[EntityResolver cari\ncommunity_id di Neo4j]
    
    E --> F{Ada nama site\nJembayan, Samarinda?}
    F -->|Ya| G[Skip community_id\nPakai ILIKE aja + filter branch_site]
    F -->|Tidak| H[Pakai community_id]
    
    D --> I[Vanna AI bikin SQL]
    G --> I
    H --> I
    
    I --> J[Cek Keamanan SQL]
    J -->|Aman| K[Inject Filter ke SQL Level]
    J -->|Berbahaya| L[Tolak!]
    
    K --> M[Inject LIMIT 100 + Jalankan]
    M --> N{Hasilnya 0?}
    N -->|Ya| O[ILIKE Fallback:\nCari pake kata kunci biasa\n+ sinonim dari community yg sama]
    N -->|Tidak| P[Tampilkan tabel (5 baris) + metadata]
    
    O --> P
    L --> Q[Balas: SQL diblokir]
`
## Input → Proses → Output

### Input
Pertanyaan bahasa Indonesia apa aja yang butuh angka, statistik, atau daftar.

Contoh:
- *"Total kerusakan hydraulic leak"*
- *"5 besar masalah di final drive"*
- *"Jumlah EMR per bulan"*

### Proses (Langkah demi Langkah)

**Langkah 1 — Resolusi Site + Account (FAST PATH)**
Sistem dulu cek apakah pertanyaan menyebut nama site (Jembayan, Samarinda) atau account (PAMA, FREEPORT):
- Kalau **ADA** → **EntityResolver DISKIP** (hemat 2 panggilan LLM + token)
- Kalau **TIDAK ADA** → lanjut ke Langkah 2 (EntityResolver)

**Langkah 2 — Cari Entity (EntityResolver)**
Pertanyaan kamu dikirim ke `EntityResolver`. Dia akan:
1. Minta LLM (AI) untuk **ekstrak kata kunci** dari pertanyaan (misal: "hydraulic leak" sebagai symptom)
2. Cocokin kata kunci itu ke database Neo4j pake **vector search + fulltext search**
3. Dapetin **canonical name** (nama resmi di database) dan **community_id**

**Langkah 3 — Cek Nama Site / Account**
Sistem juga ngecek apakah kamu menyebut nama site atau account:
- Kalau **ada nama site** → community_id **DI-SKIP**. Kenapa? Karena community_id terlalu sempit kalau digabung filter site. Pakai ILIKE aja lebih akurat.
- Kalau **ada nama account** → community_id **DI-SKIP**. Sama alasan di atas.
- Kalau **keduanya tidak ada** → community_id dipakai untuk nyari data yang relevan di semua site.

**Langkah 4 — Vanna Bikin SQL**
Pertanyaan + petunjuk dikirim ke Vanna AI. Vanna akan generate SQL.
Petunjuknya tergantung situasi:
| Situasi | Petunjuk ke Vanna |
|---------|------------------|
| Ada site | "Gunakan filter: branch_site = 'JBY'. JANGAN pakai community_id." |
| Ada account | "Gunakan filter: account_account_name = 'PAMAPERSADA NUSANTARA'. JANGAN pakai community_id." |
| Ada masalah, tanpa site/account | "Gunakan filter community_id: '1258' = ANY(community_id)" |
| Ada model aja | "JANGAN gunakan community_id — query ini murni filter model." |
| Gak ada entity | "JANGAN gunakan community_id." |

**Langkah 5 — Cek Keamanan SQL**
SQL dicek dulu:
- Harus `SELECT` atau `WITH` aja (gak boleh INSERT, DELETE, DROP, dll)
- Cuma 1 statement (gak boleh ada titik koma di tengah)
- Kalau berbahaya → ditolak

**Langkah 6 — Inject Filter di Level SQL (DEFENSE-IN-DEPTH) 🔒**
**Ini yang baru!** Setelah Vanna generate SQL, sistem **paksa inject filter** langsung ke SQL — bukan cuma hint ke Vanna. Ini *best practice industry* untuk NL-to-SQL production.

```python
# Kode di src/agent/tools.py
if site_hint and 'branch_site' not in sql:
    sql = _inject_where_condition(sql, 'branch_site', site_hint)
if account_hint and 'account_account_name' not in sql:
    sql = _inject_where_condition(sql, 'account_account_name', account_hint)
```

Contoh:
```sql
-- SQL asli dari Vanna (mungkin lupa filter):
SELECT symptom, COUNT(*) FROM emr_records GROUP BY symptom

-- Setelah inject branch_site = 'TRK':
SELECT symptom, COUNT(*) FROM emr_records 
WHERE branch_site = 'TRK'
GROUP BY symptom
```

Kalau Vanna udah bener (sudah ada filter), inject **di-skip** (idempotent).

**Langkah 7 — Inject Limit**
Kalau SQL gak ada `LIMIT`, sistem otomatis nambah `LIMIT 100` biar gak overload.

**Langkah 8 — Jalanin SQL + Fallback**
SQL dijalankan ke PostgreSQL. Kalau hasilnya 0 (kosong):
1. Sistem coba lagi pake **ILIKE** (pencarian teks biasa)
2. Kata kunci yang dipakai = dari entity yg di-resolve + **sinonim** dari community yang sama
3. Contoh: "hydraulic oil leak" → "hydraulic", "oil", "leak" → juga "oil hydraulic leak", "hydraulic leaking"

### Output
```python
{
    "answer": "Teks jawaban dalam bahasa Indonesia + tabel markdown (5 baris)",
    "sql": "SELECT symptom, COUNT(*) FROM ...", 
    "sql_data": [{"symptom": "...", "count": 5}, ...],  # ← HANYA 5 BARIS (hemat token)
    "resolved_entities": [{"mention": "hydraulic leak", "canonical_name": "HYDRAULIC OIL LEAK", ...}]
}
```

## Kode Contoh (Simplified)

```python
# File: src/agent/tools.py — fungsi ask_emr_database()

def ask_emr_database(query: str) -> dict:
    """
    1. resolve_site_mentions() + resolve_account_mentions()  → FAST PATH
    2. Kalau ada site/account: SKIP EntityResolver, bangun modified query
    3. Kalau tidak ada: EntityResolver → dapet community_id + canonical_name
    4. Vanna generate SQL dari query + petunjuk
    5. Inject community_id filter (kalau perlu)
    6. DEFENSE-IN-DEPTH: Inject branch_site / account_account_name filter DI LEVEL SQL
    7. Cek keamanan, inject LIMIT
    8. Jalanin SQL, kalau 0 → fallback ILIKE
    9. Return jawaban + data (hanya 5 baris) + provenance
    """
```

## Catatan Penting untuk Pengembang Selanjutnya

1. **Community_id itu bukan synonym group.** Leiden clustering itu ngelompokin berdasarkan graph context (model + part + symptom), BUKAN berdasarkan kesamaan teks. Jadi jangan heran kalau "Hydraulic Oil Leaks" dan "Oil Hydraulic leaks" beda community.

2. **Kalau ada site/account, community_id di-skip.** Ini sengaja. Filter site + community_id itu terlalu sempit. Site + ILIKE aja udah cukup spesifik.

3. **Synonym expansion ngebantu banget.** Setelah dapet community_id, sistem cari SEMUA entity dalam community yang sama. Jadi kalau user nyebut "hydraulic leak", sistem juga bakal nyari "Oil Hydraulic leaks", "Hydraulic Oil Leaks", "Hydraulic Pump Leaks Oil" — yang penting satu community.

4. **ILIKE fallback itu jaring pengaman.** Kalau community_id gagal (0 results), sistem gak nyerah — tetap nyoba pake pencarian teks biasa. Ini bikin fitur tetap jalan meskipun clustering-nya imperfect.

5. **SQL safety itu ketat.** Jangan khawatir soal SQL injection. Sistem udah punya `_is_safe_select_query()` yang ngeblok semua query berbahaya. Cuma SELECT yang boleh lewat.

6. **Display limit = 5 baris.** Tabel di jawaban cuma nampilin 5 baris pertama (hemat token). Statistik (numeric/categorical) tetap dihitung dari **SEMUA** data (bukan cuma 5 baris). Total record asli ditampilin di provenance.

7. **Defense-in-depth filter injection.** Jangan percaya Vanna 100% nulis filter. Sistem paksa inject `branch_site` / `account_account_name` ke SQL setelah Vanna generate. Kalau Vanna udah bener, inject di-skip. Kalau lupa, inject dijamin masuk. Ini pola standar production NL-to-SQL.

8. **EntityResolver skip kalau site/account ada.** Kalau pertanyaan "oil leak di Tarakan", sistem resolve site dulu → dapet `branch_site = 'TRK'` → **EntityResolver gak dipanggil**. Hemat ~5-10 detik + token LLM.
