# Dokumentasi Fitur: search_emr_records

## Apa yang Dilakukan Fitur Ini?

Fitur `search_emr_records` tugasnya **nyari record EMR spesifik** dan nampilin detailnya.

Ini bedanya sama tool lain:
- `ask_emr_database`: ngitung jumlah/statistik (SQL)
- `ask_emr_graph`: ngejelasin penyebab/solusi (graf)
- **`search_emr_records`: nampilin detail EMR satu-satu**

Contoh penggunaan yang pas:
- *"Cari EMR tentang oli bocor di PC200"*
- *"Tampilkan detail hydraulic leak"*
- *"EMR tentang engine overheat"*
- *"5 EMR yang ada masalah final drive"*

## Alur Kerja (Flowchart)

`mermaid
graph TD
    A[Kamu tanya:
Cari EMR tentang
hydraulic leak] --> B[EntityResolver
extract_mentions]
    
    B --> C[AI ekstrak kata kunci:
symptom: hydraulic leak
model: kalo ada]
    
    C --> D[EntityResolver
resolve_single:
cari di Neo4j pake
fulltext + vector search]
    
    D --> E[Cari EMRRecord
yang terhubung
lewat graf]
    
    E --> F{Dapet hasil?}
    
    F -->|Ya| G[Ambil 5 record
paling relevan]
    F -->|Enggak| H[Fallback:
cari pake model
pake CONTAINS]
    
    G --> I[Enrichment:
tambahin data
dari PostgreSQL
SMR, site, dll]
    H --> I
    
    I --> J[Tampilkan Markdown
5 record EMR
detail lengkap]
```

## Input → Proses → Output

### Input
Pertanyaan kamu dalam bahasa Indonesia/Inggris.

### Proses

**Langkah 1 — Ekstraksi Kata Kunci**
AI baca pertanyaan, ekstrak entity (symptom, model, component).

**Langkah 2 — Pencarian di Graf**
Setiap entity dicari di Neo4j pake fulltext + vector search. Dari entity yang cocok, sistem cari `EMRRecord` yang terhubung.

**Langkah 3 — Fallback Model**
Kalau gak dapet dari graf, sistem coba fallback:
- Cari `MachineModel` yang mirip
- Query EMR berdasarkan model pake `CONTAINS`

**Langkah 4 — Enrichment dari PostgreSQL**
Setiap EMR record yang dapet, dilengkapi dengan data tambahan dari PostgreSQL. Data ini termasuk:
- `smr_trouble` (nilai SMR, penting buat konteks)
- `branch_site` (nama site)
- `smr_direction`
- Lain-lain yang ada di PG tapi mungkin gak lengkap di Neo4j

**Langkah 5 — Format Output**
5 record teratas diformat jadi Markdown yang rapi.

### Output
String Markdown berisi detail 5 record EMR. Contoh:

```
### Record #1: U-00000158
- **Model**: PC200-10M0 | **Serial**: 12345
- **Site**: JBY | **SMR**: 1,250 jam
- **Symptom**: ENGINE OVERHEAT
- **Component**: ENGINE
- **Action**: OVERHAUL
- **Root Cause**: KONTAMINASI
- **Part**: SEAL (12345-67890)
---
```

## Kode Contoh (Simplified)

```python
# File: src/agent/tools.py

def search_emr_records(query: str) -> str:
    # 1. EntityResolver nyari entity + EMR record
    resolver = EntityResolver(graph_client)
    emrs = resolver.search_emr_records(query, limit=5)
    
    if not emrs:
        return "Maaf, gak nemu EMR yang cocok."
    
    # 2. Enrichment dari PostgreSQL
    emrs = enrich_from_postgres(emrs)
    
    return format_emr_list(emrs)
```

## Catatan Penting untuk Pengembang Selanjutnya

1. **Ini murni pencarian graf, bukan SQL.** Bedanya dengan `ask_emr_database` yang pake Vanna AI buat generate SQL, `search_emr_records` cuma njelajah node dan relasi di Neo4j.

2. **Cuma ngasih 5 record.** Ini sengaja biar jawabannya gak kepanjangan. Kalau butuh statistik yang melibatkan banyak record, pake `ask_emr_database`.

3. **Enrichment dari PostgreSQL itu baru.** Data di Neo4j mungkin gak lengkap (misal `smr_trouble` gak ada di graf). Makanya abis dapet record dari Neo4j, kita lengkapin dari PostgreSQL.

4. **Search beda sama GraphRAG.** Search ini nampilin record mentah. GraphRAG (`ask_emr_graph`) ngasih penjelasan dan analisis. Kalo kamu butuh "ngapain?" → pake search. Kalo butuh "kenapa?" → pake graph.

5. **Kalau hasilnya kosong, jangan panik.** Sistem ada fallback: nyari berdasarkan model alat. Misal user cuma bilang "PC200", sistem tetep bakal nyari EMR yang modelnya PC200.
`