# EMR Fault Analyzer (Hybrid GraphRAG + SQL)

Proyek ini adalah sistem AI asisten berbasis Hybrid GraphRAG dan SQL untuk menganalisis data rekam medis perawatan alat berat (Equipment Maintenance Records - EMR). Arsitektur ini menggabungkan pencarian semantik kualitatif pada database graf (Neo4j) dengan kemampuan agregasi data kuantitatif pada database relasional (PostgreSQL) menggunakan generator Text-to-SQL (Vanna AI) dan Azure OpenAI.

---

## Arsitektur Sistem

Sistem ini dibangun menggunakan beberapa komponen utama:
- **Inference & Embedding**: Azure OpenAI API (GPT-5.4-mini & Text-Embedding-3-Small).
- **Knowledge Graph**: Neo4j (dengan plugin Graph Data Science dan APOC untuk Leiden Community Detection).
- **Database Relasional**: PostgreSQL (untuk menyimpan data transaksi terstruktur dan agregasi).
- **SQL Generator**: Vanna AI (terlatih menggunakan skema DDL EMR).
- **Backend API**: FastAPI (dengan dukungan Circuit Breaker dan manajemen API Key).
- **Frontend UI**: Streamlit Dashboard.

---

## Dokumentasi Fitur Detail

Penjelasan mendalam mengenai setiap fitur teknis proyek ini dapat diakses secara langsung melalui tautan dokumen berikut:

- **[Fitur 1: search_emr_records](./docs/features/feature_search_emr.md)**
  - Pencarian EMR kualitatif via traversal graf Neo4j dengan fallback filter stop words.
- **[Fitur 2: ask_emr_database](./docs/features/feature_ask_emr_db.md)**
  - Eksekusi agregasi SQL kuantitatif berbasis injeksi community ID dan fallback ILIKE.
- **[Fitur 3: Entity Resolution Service](./docs/features/feature_entity_resolution.md)**
  - Resolusi penyebutan entitas dari kueri teks menjadi format node graf terstandar.
- **[Fitur 4: Graph↔SQL Sync](./docs/features/feature_graph_sql_sync.md)**
  - Sinkronisasi dua arah yang sinkron dan idempotent antara Neo4j dan PostgreSQL.
- **[Fitur 5: Graph Extraction Pipeline](./docs/features/feature_graph_extraction.md)**
  - Ekstraksi entitas EMR mentah secara paralel dan massal menggunakan LLM.
- **[Fitur 6: Community Pipeline](./docs/features/feature_community_pipeline.md)**
  - Pengelompokan komunitas semantik berbasis algoritma Leiden GDS dan rangkuman LLM.
- **[Fitur 7: GraphRAG Retrieval](./docs/features/feature_graphrag_retrieval.md)**
  - Tiga mode ekstraksi konteks semantik graf: Local, Global, dan DRIFT Search.
- **[Fitur 8: Agent Routing](./docs/features/feature_agent_routing.md)**
  - Pengatur lalu lintas intensi kueri pengguna menggunakan analisis klasifikasi LLM.
- **[Panduan Pengujian Sistem (Testing Suite)](./tests/README.md)**
  - Dokumentasi skenario pengujian unit test terstruktur per kategori fitur.

---

## Alur Data (Data Flow)

```text
[Kueri Pengguna]
       |
       v
[Agent Router] (Klasifikasi LLM)
       |
       +---> [Kualitatif/Detail] -> Entity Resolution -> Neo4j Traversal (search_emr_records)
       |
       +---> [Kuantitatif/Agregasi] -> Entity Resolution (Mendapatkan Community ID)
                                                    |
                                                    v
                                       Vanna SQL (ask_emr_database)
                                                    |
                                                    v
                                         PostgreSQL Execution
```

---

## Panduan Menjalankan Aplikasi

Ikuti langkah-langkah di bawah ini untuk memasang dan menjalankan aplikasi dari awal sampai akhir.

### Langkah 1: Persiapan Environment
1. Salin template konfigurasi variabel lingkungan:
   ```bash
   cp .env.example .env
   ```
2. Buka file `.env` yang baru dibuat dan isi kredensial yang dibutuhkan, khususnya kunci Azure OpenAI API, alamat endpoint, dan kata sandi database.

### Langkah 2: Instalasi Dependensi
1. Buat virtual environment Python:
   ```bash
   python -m venv venv
   ```
2. Aktifkan virtual environment:
   - Di Windows (PowerShell):
     ```powershell
     .\venv\Scripts\Activate.ps1
     ```
   - Di Linux / macOS:
     ```bash
     source venv/bin/activate
     ```
3. Pasang semua pustaka yang terdaftar:
   ```bash
   pip install -r requirements.txt
   ```

### Langkah 3: Menjalankan Infrastruktur Database
Jalankan instansi Neo4j dan PostgreSQL menggunakan Docker Compose:
```bash
cd docker
docker compose up -d
cd ..
```
Pastikan plugin GDS (Graph Data Science) dan APOC telah terpasang dengan benar pada instansi Neo4j Anda.

### Langkah 4: Eksekusi Ingestion Pipeline
Jalankan file Jupyter Notebook di dalam folder `notebook/` secara berurutan untuk memproses dan melatih database:
1. **`1_sql_ingestion.ipynb`**: Memuat data awal EMR ke PostgreSQL.
2. **`2_graph_extraction.ipynb`**: Mengekstrak entitas dan relasi dari teks EMR, kemudian menulisnya ke Neo4j.
3. **`3_entity_resolution.ipynb`**: Membuat indeks vector dan indeks fulltext untuk pemetaan entitas.
4. **`4_community_pipeline.ipynb`**: Menjalankan algoritma klasterisasi Leiden dan membuat rangkuman komunitas.
5. **`5_graph_to_sql_sync.ipynb`**: Menjalankan script sinkronisasi awal untuk memindahkan community_id ke database SQL.
6. **`6_vanna_training.ipynb`**: Melatih model Vanna menggunakan skema SQL dan contoh kueri.

### Langkah 5: Menjalankan Backend dan Frontend
1. Jalankan server FastAPI backend:
   ```bash
   uvicorn src.main:app --reload
   ```
2. Jalankan aplikasi Streamlit frontend pada terminal baru:
   ```bash
   streamlit run src/streamlit_app.py
   ```
3. Akses antarmuka aplikasi melalui peramban web pada alamat `http://localhost:8501`.
