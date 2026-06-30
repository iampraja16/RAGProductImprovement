# Dokumentasi Fitur: analyze_smr

## Apa yang Dilakukan Fitur Ini?

Fitur `analyze_smr` adalah tool spesial buat **ngeliat data SMR (Service Meter Reading)** dalam bentuk grafik scatter plot.

SMR itu apa? SMR adalah **jam operasi** alat berat. Misal: sebuah excavator udah dipake 5.000 jam. Nah, 5.000 itu SMR-nya.

Kenapa ini penting? Karena kita bisa liat **pada SMR berapa suatu masalah biasanya muncul**. Misal:
- Hydraulic leak biasanya muncul di SMR 2.000-5.000 jam
- Engine overheat sering di SMR 8.000-10.000 jam
- Final drive rusak di SMR 15.000+

Tool ini beda sama `ask_emr_database` karena:
1. **TANPA LIMIT** — butuh SEMUA data buat scatter plot
2. **Support filter site** + masalah sekaligus
3. **Outputnya data buat grafik**, bukan statistik

### ⚠️ PENTING: Kapan Harus Pake Tool Ini?

Tool ini otomatis dipilih kalau kamu nanya:
- Site (Jembayan, Samarinda) + masalah spesifik + SMR/jam/scatter
- Atau yang intinya minta scatter plot SMR

Contoh query yang bener:
- *"Hydraulic leak di site Bengalon + SMR"*
- *"Scatter plot SMR engine overheat di Jembayan"*
- *"Final drive leak muncul di SMR berapa?"*
- *"Cari data SMR untuk hydraulic leak"*

## Alur Kerja (Flowchart)

```mermaid
graph TD
    A[Kamu tanya:\n\"Hydraulic leak di\nBengalon + SMR\"] --> B[EntityResolver\nresolusi kata kunci\n+ site mapping]
    
    B --> C{Ada nama site?}
    C -->|Ya| D[Filter: branch_site='BGL'\n+ ILIKE hydraulic\n+ SMK tidak kosong]
    C -->|Enggak| E[Filter:\ncommunity_id atau ILIKE\n+ SMR tidak kosong]
    
    D --> F[Jalankan SQL ke\nPostgreSQL LANGSUNG\npake SQLAlchemy\nTANPA LIMIT]
    E --> F
    
    F --> G{Dapet data?}
    G -->|Ya| H[Return smr_data[]\nbuat scatter plot]
    G -->|Enggak| I[Fallback ILIKE\npake expanded names]
    
    I --> H
    
    H --> J[Streamlit render\nPlotly scatter plot\nSMR vs urutan record]
```

## Kenapa Pake SQLAlchemy Langsung, Bukan Vanna?

| Aspek | Vanna AI | SQLAlchemy Langsung |
|-------|----------|-------------------|
| **Cara kerja** | LLM generate SQL | SQL udah ditulis manual |
| **Kecepatan** | Lambat (tunggu LLM) | Cepet (langsung query) |
| **Deterministik?** | ❌ Kadang beda hasil | ✅ Hasil selalu sama |
| **LIMIT otomatis?** | Iya (dari tools.py) | **TIDAK** — kita butuh semua data |
| **Cocok buat?** | Statistik, agregasi | Scatter plot, data mentah |

Scatter plot butuh **SEMUA** record yang cocok — kalo pake LIMIT, grafiknya gak akurat. Makanya kita pake SQLAlchemy langsung.

## Input → Proses → Output

### Input
Pertanyaan yang mengandung:
- Masalah (hydraulic leak, overheat, dll) — **wajib**
- Nama site (Jembayan, Bengalon) — **opsional** (tapi sangat membantu)
- Kata kunci SMR/jam/scatter — **yang bikin router milih tool ini**

### Proses

**Langkah 1 — Entity Resolution + Site Mapping**
Sistem jalanin 2 resolusi paralel:
1. `EntityResolver.resolve_mentions_to_community_ids()` — dapetin community_id + expanded_names
2. `resolve_site_mentions()` — deteksi apakah ada nama site dalam query

**Langkah 2 — Decision: Inject Community ID atau Tidak?**
| Kondisi | Yang Dilakukan |
|---------|---------------|
| Ada site + ada masalah | ✅ Skip community_id. Pakai filter `branch_site = 'BGL'` + ILIKE symptom |
| Ada masalah doang (tanpa site) | ✅ Inject community_id: `WHERE '1258' = ANY(community_id)` |
| Ada masalah + tapi hasil 0 | ✅ Fallback: pake ILIKE + expanded_names |

**Langkah 3 — SQL Query Langsung**
Query langsung dijalankan pake SQLAlchemy (bukan Vanna):

```sql
-- Contoh: hydraulic leak di Bengalon
SELECT smr_trouble, emr_name, symptom_1, machine_model, branch_site
FROM emr_records
WHERE branch_site = 'BGL'
  AND (
    symptom_1 ILIKE '%hydraulic%' 
    OR symptom_2 ILIKE '%hydraulic%'
  )
  AND smr_trouble IS NOT NULL
ORDER BY smr_trouble ASC
```

**Langkah 4 — Fallback ILIKE (Kalau Gagal)**
Kalau hasilnya 0, sistem coba lagi dengan:
1. Expanded_names dari synonym expansion (semua entity satu community_id)
2. Pake OR antar sinonim biar lebih luas

### Output
```python
{
    "answer": "Ditemukan 12 record dengan SMR untuk hydraulic leak di Bengalon.",
    "tool_used": "analyze_smr",
    "smr_data": [
        {"smr_trouble": 1250.0, "emr_name": "U-00000158", "symptom": "OIL LEAK", "model": "PC200-10M0", "site": "BGL"},
        {"smr_trouble": 3400.0, "emr_name": "U-00000159", "symptom": "HYDRAULIC OIL LEAKS", "model": "HD785-7", "site": "BGL"},
        # ... semua record yang cocok
    ],
    "resolved_entities": {...},
    "site_hint": "BGL"
}
```

Data `smr_data[]` ini bakal dipake sama Streamlit buat render scatter plot pake Plotly.

## Kode Contoh (Simplified)

```python
# File: src/agent/tools.py

def analyze_smr(query: str) -> dict:
    # 1. Resolve entities + site
    mentions = entity_resolver.resolve_query(query)
    site_hint = resolve_site_mentions(query)
    
    # 2. Decision: pake community_id atau ILIKE?
    if site_hint:
        # Skip community_id, pake ILIKE
        records = query_pg_direct(site_hint, expanded_names)
    else:
        # Pake community_id (atau fallback)
        records = query_pg_with_community(community_ids)
    
    return {
        "smr_data": records,
        "site_hint": site_hint,
        "answer": f"Ditemukan {len(records)} record."
    }
```

## Catatan Penting Buat Junior

1. **Gak pake LIMIT.** Ini fitur satu-satunya yang gak pake LIMIT. Karena scatter plot butuh semua data. Tapi tenang, query-nya pake filter yang spesifik (site + masalah) jadi gak bakal overload.

2. **Scatter plot dibikin oleh Streamlit.** Backend cuma ngirim data mentah (`smr_data[]`). Urusan grafik (Plotly scatter plot) ditangani oleh `streamlit_app.py` di frontend.

3. **Filter SMR NOT NULL** otomatis ditambahin. Sistem gak akan ngirim record yang SMR-nya kosong, karena gak berguna buat grafik.

4. **Site filter + ILIKE tanpa community_id.** Ini sengaja. Filter site udah cukup spesifik. Ditambah community_id malah bikin terlalu sempit. Contoh: "Oil leak di JBY" → bakal nyari semua oil leak di site JBY, bukan cuma yang satu komunitas tertentu.

5. **Tool ini dipanggil LEBIH DAHULU dari tool lain.** Di router prompt, kalau user nyebut "SMR" atau "scatter" BERSAMA masalah sama site, routing prioritasnya ke `analyze_smr` dulu, baru ke tool lain.
