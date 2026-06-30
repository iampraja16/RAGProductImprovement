# Dokumentasi Fitur: Agent Routing

## Apa yang Dilakukan Fitur Ini?

Agent Routing adalah **otak yang mutusin mau pake tool mana** buat jawab pertanyaan kamu.

Bayangin gini: kamu punya 5 alat (tools) berbeda. Masing-masing alat punya keahlian sendiri:
1. **ask_emr_graph** — ahli ngasih penjelasan soal penyebab dan solusi
2. **ask_emr_database** — ahli ngitung angka dan statistik
3. **search_emr_records** — ahli nyari detail EMR spesifik
4. **analyze_smr** — ahli ngeliat grafik SMR (jam operasi)
5. **generate_executive_summary** — ahli bikin laporan PDF

Nah, Agent Routing ini tugasnya: **baca pertanyaan kamu, trus pilih alat yang paling cocok.**

## Alur Kerja (Flowchart)

```mermaid
graph TD
    A[Pertanyaan Kamu] --> B[LLM Router\nbaca RAG_ROUTER_PROMPT]
    
    B --> C{Pertanyaan ini\nminta apa?}
    
    C -->|\"Kenapa?\" \"Gimana cara?\"\n\"Apa penyebab?\"| D[ask_emr_graph\nJawab pake teori\n+ graph knowledge]
    
    C -->|\"Berapa?\" \"Top 5\"\n\"Total\" \"Paling sering\"\n\"Tren\"| E[ask_emr_database\nHitung + SQL]
    
    C -->|\"Cari EMR U-001...\"\n\"Tampilkan EMR tentang...\"\n\"Detail EMR...\"| F[search_emr_records\nCari record spesifik\ndi Neo4j]
    
    C -->|\"SMR\" \"Jam operasi\"\n\"Scatter plot\"\n\"Site X + masalah Y\n+ SMR\"| G[analyze_smr\nAmbil data SMR\nbuat scatter plot]
    
    C -->|\"Buat laporan\"\n\"Executive summary\"\n\"PDF\"| H[generate_executive_summary\nGenerate PDF report]
    
    D --> I[Output: Jawaban + konteks graf]
    E --> J[Output: Jawaban + tabel SQL]
    F --> K[Output: Detail 5 EMR record]
    G --> L[Output: Data SMR + scatter plot]
    H --> M[Output: File PDF]
```

## Aturan Routing (Yang Penting Banget)

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
1. **Router membaca prompt** — `RAG_ROUTER_PROMPT` di `src/agent/prompts.py` berisi aturan-aturan di atas
2. **LLM menganalisis** — AI baca pertanyaan kamu, cocokin sama aturan, trus pilih tool
3. **Tool dipanggil** — tool yang dipilih dijalankan dengan parameter dari pertanyaan kamu
4. **Hasil dikembalikan** — jawaban dari tool dikirim balik ke kamu

### Output
```python
{
    "response": "Jawaban dalam bahasa Indonesia...",
    "tool_used": "ask_emr_database",
    # plus data-data lain tergantung tool yang dipake
}
```

## Catatan Penting Buat Junior

1. **Routing itu pake LLM, bukan aturan if-else.** Jadi kadang hasilnya bisa beda-beda untuk pertanyaan yang mirip. Makanya kita pake prompt yang detail banget biar AI-nya konsisten.

2. **Kalau ragu, sistem pilih tool berdasarkan kata kunci.** Makanya kamu harus jeli — kalau pertanyaan mengandung kata "berarti", "total", "paling sering" → dia akan ke `ask_emr_database`. Tapi kalau mengandung "kenapa", "gimana", "penyebab" → ke `ask_emr_graph`.

3. **Site + masalah + SMR itu kombinasi spesial.** Kalau kamu nyebut ketiganya, sistem tahu itu butuh `analyze_smr`. Tool ini beda dari `ask_emr_database` karena dia **gak pake LIMIT** (butuh semua data buat grafik) dan support scatter plot.

4. **Jangan bingung antara `ask_emr_database` dan `analyze_smr`.** Keduanya sama-sama query ke PostgreSQL, tapi:
   - `ask_emr_database` → buat statistik/angka (pake Vanna AI, ada LIMIT 100)
   - `analyze_smr` → buat SMR scatter plot (SQL langsung, TANPA LIMIT)

5. **Kalau tool salah pilih, coba ulang dengan kata kunci yang lebih jelas.** Contoh: daripada "masalah hydraulic leak" (bisa masuk ke graph atau DB), lebih baik "hitung berapa hydraulic leak" (pasti ke DB) atau "kenapa hydraulic leak terjadi" (pasti ke graph).
