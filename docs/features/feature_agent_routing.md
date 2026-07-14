# Dokumentasi Fitur: Agent Planner (Ex-Router)

## Apa yang Dilakukan Fitur Ini?

Agent Planner adalah **otak yang memecah pertanyaan kamu jadi rencana eksekusi terstruktur**.

> ⚠️ **Catatan:** Dulu ini disebut "Router" (LLM milih tool langsung). Sekarang udah diganti jadi **Planner-based LangGraph Agent** — lebih reliable, structured, dan support reflection/retry.

Bayangin gini: kamu punya 5 alat (tools) berbeda. Masing-masing alat punya keahlian sendiri:
1. **ask_emr_graph** — ahli ngasih penjelasan soal penyebab dan solusi
2. **ask_emr_database** — ahli ngitung angka dan statistik
3. **search_emr_records** — ahli nyari detail EMR spesifik
4. **analyze_smr** — ahli ngeliat grafik SMR (jam operasi)
5. **generate_executive_summary** — ahli bikin laporan PDF

Nah, Agent Planner ini tugasnya: **baca pertanyaan kamu, trus bikin QueryPlan terstruktur (sub-task + tool + dependencies) yang dieksekusi secara berurutan/paralel.**

## Arsitektur Agent (LangGraph)

`mermaid
graph TD
    subgraph Agent[LangGraph Agent Pipeline]
        A[Entity Resolve] --> B[Planner\nLLM Structured Output\n→ QueryPlan]
        B --> C[Executor\nJalankan tool\nsesuai plan]
        C --> D[Aggregator\nKumpulin hasil\nsub-task]
        D --> E[Reflector\nCek kualitas jawaban\nRetry max 2x kalau kosong]
        E --> F[Composer\nSusun jawaban final\n+ Provenance]
    end
    
    User[Pertanyaan User] --> A
    F --> Answer[Jawaban Final]
`
## Alur Kerja (Flowchart)

`mermaid
graph TD
    A[Pertanyaan Kamu] --> B[Entity Resolver\nExtract entity teknis\n+ community_id]
    
    B --> C[Planner LLM\nBaca query + entity\nBikin QueryPlan]
    
    C --> D{QueryPlan:\nSub-task apa aja?}
    
    D -->|Angka / Statistik| E[ask_emr_database]
    D -->|Penyebab / Solusi| F[ask_emr_graph]
    D -->|Cari EMR Detail| G[search_emr_records]
    D -->|SMR / Scatter Plot| H[analyze_smr]
    D -->|Buat Laporan PDF| I[generate_executive_summary]
    
    E --> J[Executor jalankan\ntool paralel/sequential]
    F --> J
    G --> J
    H --> J
    I --> J
    
    J --> K[Aggregator\nGabungin hasil\nsemua sub-task]
    K --> L[Reflector\nCek: jawaban lengkap?\nAda provenance?\nKalau kosong → Retry max 2x]
    L --> M[Composer\nFormat jawaban final\n+ Provenance divider]
    M --> N[Stream ke User]
`
## Aturan Planner (Yang Penting Banget)

### Aturan 1: Tanya "Kenapa/Gimana" → `ask_emr_graph`
Kalau kamu tanya soal **penyebab, solusi, rekomendasi** — itu urusannya `ask_emr_graph`.

| Contoh Query | Tool yang Dipilih |
|-------------|------------------|
| "Apa penyebab oli bocor?" | `ask_emr_graph` |
| "Gimana cara perbaiki final drive?" | `ask_emr_graph` |
| "Masalah apa aja di komponen engine?" | `ask_emr_graph` |
| "Kenapa PC200 sering overheat?" | `ask_emr_graph` |

### Aturan 2: Tanya "Berapa/Total/Ranking" → `ask_emr_database`
Kalau kamu tanya soal **angka, statistik, perbandingan** — itu urusannya `ask_emr_database`.

| Contoh Query | Tool yang Dipilih |
|-------------|------------------|
| "Berapa total hydraulic leak?" | `ask_emr_database` |
| "Top 5 kerusakan paling sering" | `ask_emr_database` |
| "Komponen paling sering rusak" | `ask_emr_database` |
| "Tren per bulan tahun 2025" | `ask_emr_database` |

### Aturan 3: Minta "Cari/Tampilkan EMR" → `search_emr_records`
Kalau kamu minta **detail spesifik** dari record EMR — itu urusannya `search_emr_records`.

| Contoh Query | Tool yang Dipilih |
|-------------|------------------|
| "Cari EMR tentang oli bocor" | `search_emr_records` |
| "Tampilkan 5 EMR engine overheat" | `search_emr_records` |
| "Detail EMR U-00000158" | `search_emr_records` |

### Aturan 4: Tanya "SMR/Jam/Scatter" → `analyze_smr`
Kalau kamu tanya soal **service meter reading (SMR)** atau minta grafik — itu urusannya `analyze_smr`.

| Contoh Query | Tool yang Dipilih |
|-------------|------------------|
| "Hydraulic leak muncul di SMR berapa?" | `analyze_smr` |
| "Scatter plot SMR final drive leak" | `analyze_smr` |
| **"Oil leak di site Jembayan lengkap SMR"** | `analyze_smr` |
| **"Masalah hydraulic leak di site Samarinda + SMR"** | `analyze_smr` |

⚠️ **Catatan penting**: Kalau kamu nyebut **site** (Jembayan, Samarinda) **BERSAMA** masalah spesifik **DAN** SMR/jam/scatter, sistem akan otomatis pilih `analyze_smr` karena tool ini support filter site + masalah sekaligus dan kasih scatter plot.

### Aturan 5: Minta "Laporan PDF" → `generate_executive_summary`
Kalau kamu minta laporan resmi — itu urusannya `generate_executive_summary`.

| Contoh Query | Tool yang Dipilih |
|-------------|------------------|
| "Buat laporan untuk model HD785" | `generate_executive_summary` |
| "Executive summary PC200" | `generate_executive_summary` |

## Input → Proses → Output

### Input
String pertanyaan bebas dari kamu. Bisa apa aja.

### Proses

1. **Entity Resolver** — Ekstrak entity teknis (symptom, model, component) + resolve ke canonical name + community_id
2. **Planner (LLM Structured Output)** — Baca pertanyaan + entity, bikin `QueryPlan` berisi array sub-task. Masing-masing sub-task punya: `tool_name`, `sub_query`, `dependencies` (sub-task lain yang harus selesai dulu).
3. **Executor** — Jalankan sub-task sesuai plan. Bisa paralel (kalau gak ada dependency) atau sequential.
4. **Aggregator** — Kumpulin hasil dari semua sub-task jadi satu konteks besar.
5. **Reflector** — Cek kualitas jawaban: apakah ada provenance? Apakah jawaban menghubungkan data? Kalau kosong/kurang → retry (max 2x) dengan plan yang dimodifikasi.
6. **Composer** — Susun jawaban final dalam bahasa Indonesia + tampilkan `--- EVIDENCE/PROVENANCE ---` di bagian bawah.

### Output
```python
{
    "response": "Jawaban dalam bahasa Indonesia...",
    "tool_used": "ask_emr_database",  # tool utama
    "sql": "SELECT ...",
    "sql_data": [...],
    "graph_traversal": {...},
    "smr_data": [...],
    "steps": [...],  # trace eksekusi
    "token_usage": {...}
}
```

## Perbedaan Router Lama vs Planner Baru

| Aspek | Router Lama (Deprecated) | Planner Baru (Current) |
|-------|--------------------------|------------------------|
| **Output** | String nama tool (free text) | `QueryPlan` structured (JSON) |
| **Planning** | Satu tool per query | Bisa multi-subtask + dependencies |
| **Retry** | Gak ada | Ada Reflector (max 2x retry kalau kosong) |
| **Reasoning** | Implicit di prompt | Explicit di QueryPlan |
| **Debugging** | Sulit (black box) | Mudah (liat plan + trace) |
| **Parallel exec** | Tidak | Bisa (sub-task independen jalan bareng) |

## Catatan Penting untuk Pengembang Selanjutnya

1. **Planning itu pake LLM Structured Output, bukan aturan if-else.** Jadi hasilnya bisa beda-beda untuk pertanyaan yang mirip. Makanya kita pake `PLANNER_PROMPT` yang detail banget biar AI-nya konsisten.

2. **Kalau ragu, sistem pecah jadi sub-task.** Misal: "bandingkan hydraulic leak di Jembayan vs Bengalon" → Planner bisa bikin 2 sub-task `ask_emr_database` (satu untuk JBY, satu untuk BGL) → Aggregator gabungin.

3. **Reflector itu jaring pengaman.** Kalau tool balik hasil kosong, Reflector minta Planner bikin plan baru (misal: ganti tool, tambah filter, dll). Max 2 kali retry.

4. **Site + masalah + SMR itu kombinasi spesial.** Kalau kamu nyebut ketiganya, sistem tahu itu butuh `analyze_smr`. Tool ini beda dari `ask_emr_database` karena dia **gak pake LIMIT** (butuh semua data buat grafik) dan support scatter plot.

5. **Jangan bingung antara `ask_emr_database` dan `analyze_smr`.** Keduanya sama-sama query ke PostgreSQL, tapi:
   - `ask_emr_database` → buat statistik/angka (pake Vanna AI, ada LIMIT 100)
   - `analyze_smr` → buat SMR scatter plot (SQL langsung, TANPA LIMIT)

6. **Kalau tool salah pilih, coba ulang dengan kata kunci yang lebih jelas.** Contoh: daripada "masalah hydraulic leak" (bisa masuk ke graph atau DB), lebih baik "hitung berapa hydraulic leak" (pasti ke DB) atau "kenapa hydraulic leak terjadi" (pasti ke graph).

7. **Router lama (`RAG_ROUTER_PROMPT` di `prompts.py`) sudah deprecated.** Jangan dipake. Kode masih ada tapi gak dipanggil. Planner pake `PLANNER_PROMPT` + `QueryPlan` Pydantic model.
