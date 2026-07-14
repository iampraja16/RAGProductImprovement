# Dokumentasi Fitur: Site Mapping

## Apa yang Dilakukan Fitur Ini?

Fitur `site_mapping` tugasnya **nerjemahin nama site (lokasi) dari bahasa sehari-hari ke kode resmi**.

Masalahnya: di database, site pake kode 3 huruf kayak `JBY`, `BGL`, `SMD`. Tapi orang lebih familiar dengan nama panjang kayak "Jembayan", "Bengalon", "Samarinda".

Nah, fitur ini jembatannya:
- Kamu bilang **"Jembayan"** → sistem otomatis tahu itu `JBY`
- Kamu bilang **"Samarinda"** → sistem otomatis tahu itu `SMD`

## Data Site Yang Didukung

Ada **55 site** yang didukung. Contoh beberapa:

| Kode | Nama Lengkap |
|------|-------------|
| JBY | Jembayan |
| BGL | Bengalon |
| SMD | Samarinda |
| BIU | Batukajang |
| BKJ | Batukajang |
| PBB | Pembangkit |
| TBB | Tuban |
| TRK | Tarakan |
| ... | ... (total 55) |

⚠️ **Catatan:** "Batukajang" punya 2 kode (`BIU` dan `BKJ`). Ini karena dari data aslinya emang begitu.

## Alur Kerja (Flowchart)

::: mermaid
graph TD
    A[Kamu tanya:
Hydraulic leak di
site Jembayan] --> B[resolve_site_mentions
scan kata per kata
dalam query]
    
    B --> C{Cocokin ke
    SITE_MAP kode
    dan SITE_MAP_REVERSE
    nama lengkap}
    
    C -->|Cocok: Jembayan| D[Dapet:
    - site_code: JBY
    - site_name: Jembayan]
    
    C -->|Gak cocok| E[Return: None
    query tetap jalan
    normal aja]
    
    D --> F[Ubah query:
    Hydraulic leak di
    site JBY]
    
    F --> G[Inject petunjuk
    ke tool:
    Gunakan filter
    branch_site = JBY]
    
    G --> H[🔒 Defense-in-depth:
    SETELAH Vanna bikin SQL,
    sistem CEK ULANG apakah
    branch_site sudah ada di WHERE.
    Kalau belum → PAKSA inject.]
:::

## Input → Proses → Output

### Input
String pertanyaan dari kamu yang mungkin mengandung nama site.

### Proses

**Langkah 1 — Scan Kata per Kata**
Fungsi `resolve_site_mentions()` baca query kamu, trus nyari:
- Apakah ada kata yang cocok dengan **nama site** (kayak "Jembayan", "Samarinda")?
- Apakah ada kata yang cocok dengan **kode site** (kayak "JBY", "BGL")?

Yang dicari:
- 1 kata: "Jembayan" → cocok
- 2 kata: "site Jembayan" → tetap ketemu "Jembayan"
- Gak case-sensitive: "jembayan" = "Jembayan"
- Kode langsung: "JBY" → cocok

**Langkah 2 — Ubah Query + Inject Petunjuk**
Kalau ketemu site:
1. Query diubah: nama site diganti kode
2. Petunjuk dikirim: tool harus pake filter `branch_site = 'JBY'`

**Langkah 3 — Skip Community ID (Existing)**
Ini efek samping yang penting. Kalau ada site filter:
- **Community_id DI-SKIP** — karena filter site + ILIKE udah cukup spesifik
- Tool diinstruksikan: "JANGAN pake community_id, pake ILIKE aja"

**Langkah 4 — 🔒 Defense-in-depth SQL Filter Injection (BARU!)**
Ini lapisan keamanan tambahan. Setelah Vanna AI generate SQL:
1. Sistem cek: apakah kolom `branch_site` sudah ada di WHERE clause?
2. Kalau **SUDAH ADA** → baik, Vanna udah bener
3. Kalau **BELUM ADA** → sistem **PAKSA inject** `branch_site = 'JBY'` ke WHERE clause
4. Ini deterministik (gak percaya LLM 100%) — filter wajib selalu masuk

### Output
```python
{
    "site_hint": "JBY",          # Kode site yang ditemukan
    "site_name": "Jembayan",     # Nama lengkapnya
    "modified_query": "..."      # Query yang udah diubah
}
```

Kalau gak ada site yang cocok:
```python
{
    "site_hint": None,
    "site_name": None,
    "modified_query": query_original
}
```

## Dimana Fitur Ini Dipake?

| Tool | Cara Pake |
|------|-----------|
| `ask_emr_database` | Inject petunjuk ke Vanna: "Gunakan filter branch_site = 'JBY'" + **SQL-level injection** |
| `analyze_smr` | Filter langsung di SQL: `WHERE branch_site = 'JBY'` + **SQL-level injection** |
| `search_emr_records` | Filter PostgreSQL enrichment: `WHERE emr_name IN (...) AND branch_site = 'JBY'` |

## Hubungan dengan Tabel site_reference

Ada tabel `site_reference` di PostgreSQL yang nyimpen data ini:

```sql
CREATE TABLE site_reference (
    code VARCHAR(10) PRIMARY KEY,
    full_name VARCHAR(255) NOT NULL
);

INSERT INTO site_reference VALUES ('JBY', 'Jembayan');
INSERT INTO site_reference VALUES ('BGL', 'Bengalon');
INSERT INTO site_reference VALUES ('TRK', 'Tarakan');
-- ... 55 site
```

Tabel ini juga dipake sama Vanna AI pas generate SQL. Di `vanna_training/domain_docs.md` ada instruksi:
> "Untuk filter site, JOIN dengan site_reference ON emr_records.branch_site = site_reference.code"

Jadi kalau user nanya "di site Jembayan", Vanna bisa generate SQL yang bener.

## Kode Contoh (Simplified)

```python
# File: src/services/site_map.py

# Mapping: nama lengkap → kode
SITE_MAP = {
    "Jembayan": "JBY",
    "Bengalon": "BGL",
    "Samarinda": "SMD",
    "Tarakan": "TRK",
    # ... 55 site
}

# Mapping: kode → nama lengkap (buat reverse lookup)
SITE_MAP_REVERSE = {
    "JBY": "Jembayan",
    "BGL": "Bengalon",
    "TRK": "Tarakan",
    # ...
}

def resolve_site_mentions(query: str) -> dict:
    """Cari nama site dalam query. Return site_hint kalo ketemu."""
    for word in query.split():
        # Cek apakah kata ini cocok dengan nama site atau kode site
        if word in SITE_MAP:
            return {"site_hint": SITE_MAP[word], "site_name": word}
        if word.upper() in SITE_MAP_REVERSE:
            return {"site_hint": word.upper(), "site_name": SITE_MAP_REVERSE[word.upper()]}
    return {"site_hint": None, "site_name": None}
```

```python
# File: src/agent/tools.py — fungsi _inject_where_condition() (BARU)

def _inject_where_condition(sql: str, column_name: str, condition_str: str) -> str:
    """Inject WHERE condition di SQL level. Deterministik, gak percaya LLM."""
    # 1. Kalau kolom sudah ada di SQL → skip (Vanna udah bener)
    # 2. Cari posisi WHERE / GROUP BY / ORDER BY / LIMIT
    # 3. Inject condition di tempat yang bener
    # 4. Return SQL dengan filter yang dipastikan masuk
    ...
```

## Catatan Penting untuk Pengembang Selanjutnya

1. **Site mapping dipisah dari EntityResolver.** Ini sengaja. EntityResolver urusan sama entity teknis (symptom, model, component). Site mapping urusan lokasi. Dipisah biar kode lebih rapi dan gampang di-test.

2. **Satu nama site bisa punya 2 kode.** Contoh: "Batukajang" ada di kode `BIU` dan `BKJ`. Ini karena dari data CSV aslinya emang ada duplikat. Sistem tetep pake kode yang pertama ketemu.

3. **Site filter bikin community_id di-skip.** Logikanya: kalau kamu udah filter site tertentu, ditambah community_id malah bikin terlalu sempit. Misal: "Hydraulic leak di Jembayan" — filter site JBY + ILIKE hydraulic udah cukup spesifik.

4. **Ada juga Account Mapping.** Fitur yang mirip — tapi buat customer/account. Bedanya site pake hardcode (55 site), account pake dynamic load dari database (1.193 account). Lihat [`feature_account_mapping.md`](feature_account_mapping.md).

5. **Migration tabel site_reference** ada di `scripts/migrate_site_lookup.py`. Jalanin dulu sebelum pake fitur ini. Datanya dari `data/plottingSite.csv`.

6. **Case insensitive.** "jembayan", "Jembayan", "JEMBAYAN" — semuanya ketemu.

7. **🔒 Defense-in-depth filter injection (BARU).** Filter site DIJAMIN masuk ke SQL — bukan cuma "hint" buat Vanna. Sistem cek ulang setelah Vanna generate, kalau belum ada → paksa inject. Ini best practice industry buat production NL-to-SQL system.
