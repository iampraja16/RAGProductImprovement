# Dokumentasi Fitur: Account / Customer Mapping

## Apa yang Dilakukan Fitur Ini?

Fitur `account_mapping` tugasnya **mendeteksi nama customer/company dari pertanyaan kamu dan inject filter yang tepat**.

Masalahnya: user sering nyebut singkatan kayak "PAMA", "FREEPORT", "ADARO" — tapi di database nama lengkapnya "PAMAPERSADA NUSANTARA", "FREEPORT INDONESIA", "ADARO INDONESIA".

Nah, fitur ini jembatannya:
- Kamu bilang **"PAMA"** → sistem otomatis tahu itu `PAMAPERSADA NUSANTARA`
- Kamu bilang **"FREEPORT"** → sistem otomatis tahu itu `FREEPORT INDONESIA`
- Kamu bilang **"PAMAPERSADA NUSANTARA"** langsung → juga ketemu (full name match)
- Semua **1.193 account** di database ter-cover secara otomatis

## Gak Ada Hardcode — Dinamis dari Database

Bedanya sama Site Mapping (yang pake daftar 55 site hardcoded), Account Mapping **load data langsung dari PostgreSQL** pas pertama kali dipanggil:

```python
# Setiap kali server start / pertama kali dipanggil:
SELECT DISTINCT account_account_name FROM emr_records ORDER BY account_account_name
# → 1.193 unique account names
```

Jadi:
- **Gak perlu maintain list manual** — datanya selalu sinkron sama database
- **Account baru otomatis terdeteksi** — tinggal insert data EMR baru
- **Cocok buat dataset dengan 1.000+ account** — beda sama site yang cuma 55

## Alur Kerja (Flowchart)

`mermaid
graph TD
    A[Kamu tanya:\nengine overheat\ndi PAMA] --> B[resolve_account_mentions\nload 1.193 account\ndari PostgreSQL\n(1x aja)]
    
    B --> C{Cocokin kata kunci\nke semua account\n(case insensitive)}
    
    C -->|Strategy 1:\nfull/partial name\nada di query| D["PAMAPERSADA NUSANTARA"\n→ langsung match]
    
    C -->|Strategy 2:\ntoken query ada\ndi account name| E["PAMA" → ada di\n"PAMAPERSADA NUSANTARA"\n→ match]
    
    D --> F[Dapet hint:\naccount_account_name =\n'PAMAPERSADA NUSANTARA']
    E --> F
    
    C -->|Gak ada yang cocok| G[Return: None\nquery tetap jalan\nnormal aja]
    
    F --> H[🔒 Defense-in-depth:\nSETELAH Vanna bikin SQL,\nsistem CEK ULANG apakah\naccount_account_name sudah\ndi WHERE. Kalau belum → PAKSA inject.]
`
## Input → Proses → Output

### Input
String pertanyaan dari kamu yang mungkin mengandung nama customer/company.

### Proses

**Langkah 1 — Load Account Names**
Pas pertama kali dipanggil, fungsi `resolve_account_mentions()` ngambil semua nama account unik dari PostgreSQL. Hasilnya di-cache di memory — jadi panggilan berikutnya langsung aja.

**Langkah 2 — Matching (2 Strategi)**

| Strategi | Cara Kerja | Contoh |
|----------|-----------|--------|
| **1: Full/partial name in query** | Apakah nama account (lowercase) ada di query? | "PAMAPERSADA NUSANTARA" di "engine overheat di PAMAPERSADA NUSANTARA" → match |
| **2: Token in account name** | Apakah token dari query ada di nama account? | "PAMA" → cek di semua 1.193 account → "PAMAPERSADA NUSANTARA" mengandung "PAMA" → match |

Prioritas: Strategy 1 duluan, baru Strategy 2. Hasilnya digabung.

**🛡️ False Positive Protection (BARU!)**
Strategy 2 punya **skip list** kata-kata yang GAK boleh trigger account match:
```python
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
})
```

Contoh masalah yang diperbaiki:
- Query: `"5 masalah yang sering terjadi di site Bengalon"`
- Token `"site"` dulu match ke `"MUHAMMAD JAYA SITEPU"` (substring "site" in "sitepu") → **FALSE POSITIVE**
- Sekarang: `"site"` ada di `_QUERY_SKIP_WORDS` → **di-skip**, tidak trigger account filter

**Langkah 3 — Filter Injection**
Kalau ketemu, hint dikirim ke tool:
- Single match: `account_account_name = 'PAMAPERSADA NUSANTARA'`
- Multiple match: `account_account_name = 'ADARO INDONESIA' OR account_account_name = 'ADARO LOGISTICS'`

**Langkah 4 — Community ID Skip**
Sama kayak site filter: kalau ada account filter, **community_id di-skip** — karena filter account + ILIKE udah cukup spesifik.

**Langkah 5 — 🔒 Defense-in-depth SQL Filter Injection (BARU!)**
Ini lapisan keamanan tambahan. Setelah Vanna AI generate SQL:
1. Sistem cek: apakah kolom `account_account_name` sudah ada di WHERE clause?
2. Kalau **SUDAH ADA** → baik, Vanna udah bener
3. Kalau **BELUM ADA** → sistem **PAKSA inject** filter account ke WHERE clause
4. Ini deterministik (gak percaya LLM 100%) — filter wajib selalu masuk

### Output
```python
# Kalau ketemu:
("engine overheat di ...", "account_account_name = 'PAMAPERSADA NUSANTARA'")

# Kalau gak ketemu:
("engine overheat di ...", None)
```

## Dimana Fitur Ini Dipake?

| Tool | Cara Pake |
|------|-----------|
| `ask_emr_database` | Inject petunjuk ke Vanna: "Gunakan filter: account_account_name = 'PAMAPERSADA NUSANTARA'" + **SQL-level injection** |
| `search_emr_records` | Filter PostgreSQL enrichment: `WHERE emr_name IN (...) AND (account_account_name = '...')` + **SQL-level injection** |
| `analyze_smr` | Filter langsung di SQL: `AND (account_account_name = '...')` + **SQL-level injection** |

Ketiga tool juga otomatis nge-skip community_id kalau account_hint aktif.

## Hubungan dengan Tabel account_reference

Ada tabel `account_reference` di PostgreSQL untuk referensi:

```sql
CREATE TABLE account_reference (
    full_name VARCHAR(200) PRIMARY KEY
);

-- Isinya: 1.193 baris dari DISTINCT account_account_name
```

Tabel ini dibuat oleh `scripts/migrate_account_lookup.py`. Sama kayak `site_reference`, tabel ini juga dipake sama Vanna AI pas generate SQL.

## Kombinasi dengan Site Mapping

Account mapping jalan barengan sama site mapping. Contoh:

```
Query: "engine overheat di PAMA site Jembayan"
    ↓
resolve_site_mentions → hint: branch_site = 'JBY'
resolve_account_mentions → hint: account_account_name = 'PAMAPERSADA NUSANTARA'
    ↓
Kombinasi: "Gunakan filter: branch_site = 'JBY' AND account_account_name = 'PAMAPERSADA NUSANTARA'"
    ↓
SQL: WHERE branch_site = 'JBY' AND account_account_name = 'PAMAPERSADA NUSANTARA'
```

Keduanya AND — artinya hasil cuma record yang cocok BUKTI site DAN account.

## Kode Contoh (Simplified)

```python
# File: src/services/account_map.py

# Module-level cache
_ACCOUNT_NAMES = []  # Diisi dari PostgreSQL pas pertama dipanggil

def _load_account_names():
    """Load 1.193 account names dari PostgreSQL."""
    # SELECT DISTINCT account_account_name FROM emr_records
    # ORDER BY account_account_name
    ...

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
})

def resolve_account_mentions(query: str):
    """Cari account dalam query. Return hint kalo ketemu."""
    # Cek apakah query match dengan akun mana pun
    for token in query_tokens:          # "PAMA"
        if token in _QUERY_SKIP_WORDS:  # 🛡️ BARU: skip false positive
            continue
        for account in ACCOUNT_NAMES:    # "PAMAPERSADA NUSANTARA"
            if token in account.lower():  # "pama" in "pamapersada nusantara" → True
                found.append(account)
    
    if found:
        hint = " OR ".join(f"account_account_name = '{a}'" for a in found)
        return query, hint
    return query, None
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

1. **Tidak ada hardcode 1.193 account.** Bedanya sama site_map (55 site hardcoded), account_map load dari database setiap server start. Ini sengaja biar gak perlu maintain list manual yang kebanyakan.

2. **Case insensitive.** "pama", "Pama", "PAMA" — semuanya ketemu ke "PAMAPERSADA NUSANTARA".

3. **Multiple match dimungkinkan.** "ADARO" bisa match "ADARO INDONESIA" DAN "ADARO LOGISTICS". Hint jadi OR.

4. **Account filter skip community_id.** Sama kayak site filter — kalau udah filter account, community_id gak dipake biar hasil gak terlalu sempit.

5. **Migration tabel account_reference** ada di `scripts/migrate_account_lookup.py`. Datanya dari `data/account_lookup.csv` (export dari database).

6. **🛡️ False positive fix (BARU).** Token `"site"` dulu match `"SITEPU"` → false positive. Sekarang `"site"` di `_QUERY_SKIP_WORDS` → di-skip. Juga `"engine"` (match "engineering"), `"data"`, `"info"`, `"masalah"`, dll. List lengkap di `_QUERY_SKIP_WORDS`.

7. **🔒 Defense-in-depth filter injection (BARU).** Filter account DIJAMIN masuk ke SQL — bukan cuma "hint" buat Vanna. Sistem cek ulang setelah Vanna generate, kalau belum ada → paksa inject. Ini best practice industry buat production NL-to-SQL system.
