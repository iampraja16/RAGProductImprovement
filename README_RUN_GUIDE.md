# Panduan Menjalankan Sistem EMR Fault Analyzer (Pure GraphRAG)

Sistem ini adalah pipeline Hybrid RAG (Graph + SQL) yang mutakhir. Panduan ini menjelaskan urutan yang benar dari inisialisasi infrastruktur hingga interaksi di antarmuka pengguna (UI).

---

## Tahap 1: Inisialisasi Infrastruktur (Database & Cache)
Karena Anda menggunakan arsitektur berbasis *container*, seluruh *database engine* (PostgreSQL, Neo4j, Qdrant, Redis) dan lokal LLM (Ollama) harus dihidupkan terlebih dahulu.

1. Buka terminal di folder root proyek (tempat file `docker-compose.yml` berada).
2. Jalankan perintah:
   ```bash
   podman-compose up -d
   ```
   *(Atau `docker-compose up -d` jika menggunakan Docker)*
3. Tunggu beberapa saat agar Neo4j mengunduh dan memasang plugin **Graph Data Science (GDS)** secara otomatis.
4. Pastikan model lokal Ollama sudah terunduh dengan menjalankan:
   ```bash
   podman exec -it ollama ollama run qwen2.5:3b
   ```

---

## Tahap 2: Ingesti Data & GraphRAG Pipeline (Notebooks)
Proses transformasi EMR dari teks mentah menjadi *Knowledge Graph* hierarkis sepenuhnya diotomatisasi melalui Jupyter Notebook.

**Jalankan notebook di folder `notebook/` secara berurutan persis seperti ini:**

1. **`1_sql_ingestion.ipynb`**
   - Membaca file CSV EMR mentah dari folder `data/` dan memuatnya ke tabel `emr_records` di PostgreSQL.
2. **`2_graph_extraction.ipynb`**
   - Agent LLM akan mengekstrak entitas (*SymptomPattern*, *RootCausePattern*, dll) dari teks mentah EMR secara otonom dan menyuntikkannya ke Neo4j sebagai graf.
3. **`3_entity_resolution.ipynb`**
   - Pipeline AI secara otomatis akan menggabungkan (*merge*) entitas yang mirip atau yang salah ketik (typo) di dalam Neo4j menggunakan *vector embedding*.
4. **`4_community_pipeline.ipynb`**
   - Menjalankan **Hierarchical Leiden Algorithm** via GDS untuk mendeteksi komunitas masalah secara makro.
   - LLM akan merangkum (summarize) masing-masing komunitas mulai dari masalah mikro hingga *executive level*.
5. **`5_graph_to_sql_sync.ipynb`**
   - Skrip yang akan mengambil hasil rangkuman komunitas tertinggi dari Neo4j dan menyuntikkannya kembali ke kolom `graph_community_summary` di PostgreSQL.
6. **`6_vanna_training.ipynb`**
   - Melatih asisten Vanna (AI pembuat SQL) agar ia mengenali kolom komunitas baru tersebut dan menyimpan otaknya di Qdrant.

*(Catatan: Jangan jalankan file yang ditandai dengan `[DEPRECATED]` karena itu adalah algoritma konvensional lama yang sudah dipensiunkan).*

---

## Tahap 3: Konfigurasi Indeks Pencarian Neo4j (Penting!)
Setelah data masuk ke Neo4j, kita harus membuat *Vector Index* dan *Full-text Index* agar fitur pencarian bekerja dengan sangat cepat.

1. Buka terminal.
2. Jalankan skrip setup yang sudah dibuat:
   ```bash
   python scripts/setup_indexes.py
   ```
3. Skrip ini akan mencetak log `CREATED` untuk memastikan semua indeks pencarian GraphRAG sudah aktif.

---

## Tahap 4: Menjalankan Server Backend (API & Agent)
Sekarang "otak" dari sistem (LangGraph Agent) siap dinyalakan.

1. Buka terminal baru.
2. Aktifkan *virtual environment* Python Anda (jika ada).
3. Jalankan server FastAPI:
   ```bash
   uvicorn src.main:app --reload
   ```
4. Server akan berjalan di `http://localhost:8000`. Saat *startup*, Anda akan melihat log memuat model *embedding* dan menginisialisasi cache.

---

## Tahap 5: Menjalankan Frontend UI (Streamlit)
Langkah terakhir adalah membuka antarmuka obrolan (*chat interface*) agar Anda bisa mulai bertanya ke *Copilot*.

1. Buka terminal baru lagi.
2. Jalankan aplikasi Streamlit:
   ```bash
   streamlit run src/streamlit_app.py
   ```
3. Browser akan otomatis terbuka menampilkan EMR Fault Analyzer.

---

## Cara Penggunaan di UI
Di antarmuka Streamlit, Anda akan melihat **Settings** di bilah sisi kiri (*sidebar*):
1. Cek status koneksi backend. Pastikan semua *database* bertuliskan `OK`.
2. Pilih **Graph Retrieval Mode**:
   - **DRIFT (Default):** Jika Anda bertanya sesuatu secara umum tapi butuh jawaban detail ("Apa masalah umum pada engine seri 123?").
   - **Local:** Jika Anda bertanya masalah yang sangat spesifik tentang 1 suku cadang ("Apa fungsi part 12345?").
   - **Global:** Jika Anda bertanya gambaran tingkat atas ("Bagaimana kondisi kesehatan armada kita bulan ini?").
3. Ketikkan pertanyaan di kolom *chat*, dan Anda akan melihat *reasoning trace* (proses berpikir LLM memilih alat pencarian) beserta visualisasi graf relasinya secara interaktif!
