# Migrasi ke Agentic GraphRAG: Arsitektur, Analisis, dan Rencana Implementasi

**Konteks:** Sistem EMR (Equipment Maintenance Record) chatbot dengan 5 tools (Vanna/Postgres, Neo4j GraphRAG+Leiden, Neo4j record retriever, SMR/HM visualizer, report generator), diorkestrasi via LangGraph dengan 1 LLM router.

**Gejala utama:**
1. Jawaban "aneh"/tidak sinkron saat pertanyaan bersifat **kombinasional** (kuantitatif + kualitatif sekaligus).
2. Sistem **sensitif terhadap perubahan prompt** (tidak robust/konsisten).

---

## 1. Root Cause Analysis (kenapa arsitektur sekarang gagal)

Arsitektur Anda saat ini adalah **"Single-Shot Tool Router"**, bukan agent sungguhan:

```
User Query → [1 LLM call: pilih tool] → [Tool dieksekusi] → Jawaban
```

Ini gagal untuk pertanyaan kombinasional karena beberapa alasan struktural:

| # | Root Cause | Dampak |
|---|---|---|
| 1 | **Tidak ada Query Decomposition.** Router mencoba memetakan 1 pertanyaan kompleks → 1 tool, padahal pertanyaan seperti contoh Anda sebenarnya berisi **5-6 sub-pertanyaan** (problem umum, penyebab, langkah perbaikan, komponen rusak, jumlah per cabang/site, kesimpulan). | Router memaksakan salah satu tool untuk menjawab semuanya, atau memilih tool yang salah → jawaban tidak lengkap/ngawur. |
| 2 | **Tidak ada Entity/Context Resolver terpusat.** Setiap tool (`ask_emr_database`, `ask_emr_graph`, dst) menginterpretasikan sendiri entitas seperti `HD785-7`, rentang tanggal, site, cabang — dari teks mentah masing-masing. | SQL Agent bisa menghasilkan filter `model = 'HD785-7'` sementara Graph Agent mencari node `HD785`. Hasil dari 2 sumber data **tidak sinkron** karena filter berbeda. Ini persis akar masalah "sinkronisasi antar database" yang Anda sebutkan. |
| 3 | **Tidak ada eksekusi paralel + agregasi.** LangGraph Anda tampaknya "route once, call once". | Padahal butuh: panggil SQL Agent DAN Graph Agent secara paralel dengan entitas yang **sama**, lalu satukan hasilnya di satu langkah sintesis. |
| 4 | **Tidak ada Reflection/Verification step.** Tidak ada yang mengecek apakah semua bagian pertanyaan sudah terjawab sebelum dikirim ke user. | Jawaban parsial lolos ke user tanpa terdeteksi. |
| 5 | **Prompt sensitivity** = router bergantung pada satu prompt klasifikasi bebas-teks (free-text), tanpa structured output / schema / few-shot examples yang terkontrol, tanpa eval harness. | Perubahan kecil pada prompt sistem mengubah keputusan routing secara tidak terduga — klasik **prompt brittleness** pada LLM-as-router tanpa structured constraint. |
| 6 | **Tidak ada shared state/memory antar tool call dalam 1 turn.** | Tool kedua tidak tahu apa yang sudah ditemukan tool pertama → tidak bisa saling melengkapi atau cross-check. |

**Kesimpulan:** Anda **memang perlu bermigrasi ke pola Agentic GraphRAG** — tapi bukan sekadar "menambah tools", melainkan mengubah *pola orkestrasi* dari **single-shot routing** menjadi **plan → parallel-execute → synthesize → verify (reflection loop)**. LangGraph Anda sudah tepat sebagai framework; yang perlu diubah adalah *graph topology*-nya.

---

## 2. Apakah Perlu "Agentic GraphRAG"? — Keputusan

**Ya**, dengan definisi yang presisi supaya tidak over-engineering:

- **Bukan** agentic = biarkan LLM bebas memutuskan berapa kali tool dipanggil tanpa batas (loop tak terkendali, mahal, tidak predictable untuk production).
- **Agentic yang tepat untuk kasus Anda** = *Bounded Plan-Execute-Reflect Agent*:
  - LLM **merencanakan** sub-tugas dari 1 pertanyaan (planning eksplisit, bukan implisit).
  - Sub-tugas independen dieksekusi **paralel** (fan-out), bukan sequential blind chaining.
  - Ada **budget/step limit** (mis. maksimal 2 iterasi reflection) agar tidak infinite loop dan biaya token terkendali.
  - Ada **verifier/critic** yang mengecek kelengkapan jawaban terhadap checklist dari planner — bukan LLM bebas mengevaluasi dirinya sendiri tanpa struktur.

Ini disebut **Agentic GraphRAG** karena keputusan *retrieval path* (SQL vs Graph vs hybrid vs berapa hop) dibuat secara dinamis oleh agent berdasarkan struktur pertanyaan, bukan hardcoded satu tool per intent.

---

## 3. Arsitektur yang Disarankan

### 3.1 Diagram Alur (Mermaid)

::: mermaid
flowchart TD
    A[User Query] --> B[Node: Entity & Context Resolver]
    B --> C[Node: Query Planner / Decomposer]
    C --> D{Butuh berapa sub-task?}
    D -->|1 sub-task sederhana| E[Direct Single Tool Call]
    D -->|Multi sub-task / kombinasional| F[Fan-out Parallel Tool Execution]

    F --> F1[ask_emr_database]
    F --> F2[ask_emr_graph]
    F --> F3[search_emr_records]
    F --> F4[analyze_smr]

    F1 --> G[Node: Aggregator / Synthesizer]
    F2 --> G
    F3 --> G
    F4 --> G
    E --> G

    G --> H[Node: Reflection / Completeness Checker]
    H -->|Ada bagian pertanyaan belum terjawab| C
    H -->|Lengkap| I[Node: Final Answer Composer]
    I --> J{Perlu report file?}
    J -->|Ya| K[generate_executive_summary]
    J -->|Tidak| L[Jawaban ke User]
    K --> L
```

### 3.2 Komponen Baru (bukan "tool" tapi *graph node* di LangGraph)

| Node Baru | Peran | Kenapa Wajib Ada |
|---|---|---|
| **Entity & Context Resolver** | Ekstrak entitas terstruktur (model unit, site, cabang, rentang tanggal, no. EMR) dari query mentah **satu kali**, output sebagai JSON/Pydantic object. | Memastikan `ask_emr_database` dan `ask_emr_graph` memakai **filter identik** → menyelesaikan masalah sinkronisasi database Anda secara langsung. |
| **Query Planner / Decomposer** | Memecah 1 pertanyaan kompleks menjadi daftar sub-task terstruktur, masing-masing dengan `tool_hint`, `sub_question`, dan `entities` (turunan dari resolver). Output wajib **structured output** (Pydantic/JSON schema), bukan free text. | Mengatasi akar masalah #1 — jawaban kombinasional butuh multi-tool, bukan 1 pilihan. |
| **Parallel Fan-out Executor** | Menjalankan beberapa tool secara bersamaan (LangGraph `Send` API) untuk sub-task independen. | Efisiensi latensi + memastikan semua sub-pertanyaan benar-benar dieksekusi, bukan diabaikan router. |
| **Aggregator / Synthesizer** | Menggabungkan hasil semua tool ke dalam satu context terstruktur sebelum LLM menulis jawaban akhir. Idealnya template per section (Problem Umum / Penyebab / Perbaikan / Komponen / Jumlah per Cabang-Site / Kesimpulan). | Mencegah LLM "mengarang" narasi bebas dari hasil tool yang tercerai-berai. |
| **Reflection / Completeness Checker** | Bandingkan checklist dari Planner vs isi jawaban akhir. Jika ada sub-task yang belum terjawab/hasil kosong → loop balik ke Planner (maks 2x) untuk retry dengan strategi berbeda. | Mengatasi akar masalah #4 — mencegah jawaban parsial lolos ke user. |

### 3.3 Mengatasi Prompt Sensitivity

Prompt sensitivity biasanya berasal dari router berbasis **free-text classification**. Solusi konkret:

1. **Structured Output wajib** untuk semua keputusan routing/planning — gunakan `with_structured_output()` (LangChain) atau `instructor` dengan Pydantic schema. LLM tidak lagi "mengarang" format jawaban routing.
2. **Few-shot examples per kategori intent** (quantitative-only, qualitative-only, listing, SMR/HM, kombinasional, report) disematkan di system prompt Planner — minimal 2-3 contoh per kategori, termasuk contoh kombinasional persis seperti kasus HD785-7 Anda.
3. **Eval harness / regression test set**: kumpulkan 30-50 pertanyaan riil (termasuk variasi kalimat untuk pertanyaan yang sama) → jadikan golden dataset. Setiap kali system prompt/router diubah, jalankan otomatis dan bandingkan tool-selection accuracy sebelum deploy. Tanpa ini, Anda akan terus "menambal" prompt secara reaktif.
4. **Confidence/ambiguity fallback**: jika Planner tidak yakin (entitas tidak ditemukan / ambigu), agent bertanya klarifikasi ke user alih-alih menebak.
5. **Version pinning system prompt** dan simpan sebagai artifact terpisah dari kode (bukan inline string tersebar) agar mudah di-diff dan di-rollback.

---

## 4. Perubahan Konkret: Before → After

| Aspek | Sekarang | Setelah Migrasi |
|---|---|---|
| Jumlah LLM decision point per query | 1 (router) | 3 (resolver, planner, reflection) — tapi dengan step-budget terbatas |
| Cara menangani entitas (model unit, site) | Diinterpretasikan ulang oleh tiap tool | Diresolusi 1x, dipakai bersama (shared state) |
| Eksekusi tool untuk query kombinasional | Sequential/tunggal (kadang salah pilih) | Paralel (fan-out), lengkap sesuai planner |
| Format keputusan routing | Free-text/implisit | Structured output (Pydantic/JSON schema) |
| Validasi jawaban akhir | Tidak ada | Node reflection dengan checklist |
| Testing terhadap perubahan prompt | Manual, reaktif | Eval harness otomatis (regression set) |
| Observability | Minim | Tracing per node (LangSmith/OpenTelemetry) |
| Tools yang ada | 5 tools tetap dipakai apa adanya | 5 tools **tetap dipakai**, tidak dibuang — hanya ditambah *orchestration layer* di sekitarnya |

**Catatan penting:** Anda **tidak perlu membuang/mengganti** Vanna, Postgres, Neo4j, atau Qdrant. Perubahan utama ada di **lapisan orkestrasi LangGraph** (topology graph-nya), bukan di layer database/retrieval.

---

## 5. Contoh Alur untuk Query Kasus Anda

Query: *"Berikan informasi terkait problem yang sering terjadi pada unit model HD785-7 disertai dengan penyebab kerusakan, langkah perbaikan, komponen yang rusak, jumlah problem pada setiap cabang dan site serta berikan kesimpulan komponen mana yang sering mengalami kerusakan pada model unit tersebut secara jumlah."*

1. **Resolver** → `{"unit_model": "HD785-7", "site": null, "branch": null, "date_range": null}`
2. **Planner** memecah menjadi:
   - Sub-task A (kualitatif) → `ask_emr_graph`: "problem umum, penyebab kerusakan, langkah perbaikan, komponen rusak untuk HD785-7"
   - Sub-task B (kuantitatif) → `ask_emr_database`: "jumlah problem per cabang dan site untuk HD785-7" + "ranking komponen paling sering rusak (by count) untuk HD785-7"
3. **Fan-out**: A dan B dieksekusi paralel, keduanya menerima `unit_model = "HD785-7"` yang identik.
4. **Aggregator** menyusun hasil ke dalam kerangka: Problem Umum | Penyebab | Langkah Perbaikan | Komponen Rusak | Tabel Jumlah per Cabang/Site | Ranking Komponen.
5. **Reflection** cek: apakah "kesimpulan komponen mana yang sering rusak secara jumlah" sudah terjawab dari hasil B? Jika data ranking ada → lengkap. Jika tidak → balik ke Planner untuk memicu ulang `ask_emr_database` dengan query lebih spesifik.
6. **Composer** merangkai narasi akhir dari data terstruktur (bukan LLM mengarang bebas) → dikirim ke user.

---

## 6. Rencana Implementasi Bertahap

### Fase 0 — Fondasi Evaluasi (lakukan SEBELUM ubah arsitektur)
- [ ] Kumpulkan 30-50 query nyata dari user (campur: quantitative-only, qualitative-only, listing, SMR, kombinasional, variasi kalimat).
- [ ] Buat golden answer/expected-tool-selection untuk tiap query.
- [ ] Setup eval script sederhana (bisa pakai `ragas`, atau custom scorer: tool selection accuracy + faithfulness jawaban).
- [ ] Jalankan terhadap arsitektur lama untuk baseline metric.

### Fase 1 — Entity Resolver + Structured Planner
- [ ] Tambah node `resolve_entities` di awal graph (Pydantic schema: unit_model, site, branch, date_range, emr_number).
- [ ] Ganti router bebas-teks dengan node `plan_subtasks` yang output-nya list of `SubTask(tool, sub_question, entities)` via structured output.
- [ ] Untuk query sederhana (1 sub-task), tetap jalur cepat (tidak perlu full loop) agar latensi tidak naik untuk kasus mudah.
- [ ] Re-run eval Fase 0 → ukur peningkatan tool-selection accuracy.

### Fase 2 — Parallel Fan-out Execution
- [ ] Implementasikan `Send()` API LangGraph untuk fan-out ke tool-tool sesuai daftar sub-task.
- [ ] Pastikan semua tool call dalam 1 fan-out menerima entities yang sama dari resolver (bukan re-parsing sendiri).
- [ ] Tambah node `aggregate_results` yang menyatukan output tiap tool ke dalam struktur section-based sebelum masuk ke LLM composer.

### Fase 3 — Reflection Loop
- [ ] Tambah node `check_completeness`: LLM/rule-based membandingkan checklist sub-task planner vs isi aggregated result.
- [ ] Set step-budget (mis. maksimal 2 kali retry) untuk mencegah infinite loop dan membengkaknya biaya.
- [ ] Tambah fallback: jika setelah 2 retry masih tidak lengkap, jawab sebagian + informasikan ke user bagian mana yang tidak tersedia (jangan mengarang).

### Fase 4 — Robustness & Observability
- [ ] Pindahkan semua system prompt ke file terpisah (versioned), tambahkan few-shot examples per kategori intent.
- [ ] Integrasikan tracing (LangSmith atau OpenTelemetry) per node untuk debugging "kenapa jawaban aneh" jadi mudah ditelusuri sub-task mana yang gagal.
- [ ] Jadikan Fase 0 eval-set sebagai **regression test wajib** sebelum setiap perubahan prompt/model dideploy ke production (CI check).
- [ ] Tambah caching untuk pola pertanyaan yang sering berulang (mis. cache hasil entity resolution + SQL query template per kombinasi unit_model+site).

### Fase 5 — Optimisasi Lanjutan (opsional, setelah stabil)
- [ ] Confidence scoring pada resolver: jika entitas ambigu, agent bertanya balik ke user.
- [ ] Hybrid retrieval scoring antara Qdrant (semantic) dan Neo4j (graph traversal) jika ada overlap fungsi pencarian semantik.
- [ ] A/B test perubahan prompt menggunakan eval-set sebelum full rollout.

---

## 7. Risiko & Mitigasi

| Risiko | Mitigasi |
|---|---|
| Latensi naik karena lebih banyak LLM call (resolver, planner, reflection) | Gunakan model kecil/cepat untuk resolver & reflection (bukan model paling mahal); jalur cepat untuk query sederhana (skip planner jika 1 intent jelas). |
| Biaya token naik | Step-budget ketat, caching, fan-out hanya untuk sub-task yang benar-benar independen. |
| Kompleksitas maintenance graph bertambah | Modularisasi tiap node sebagai fungsi/file terpisah, dokumentasikan schema Pydantic sebagai kontrak antar node. |
| Reflection loop bisa "tidak yakin-yakin" | Batasi maksimal 2 iterasi, fallback jawab parsial dengan disclaimer daripada infinite retry. |

---

## 8. Ringkasan Keputusan

- **Ya**, migrasi ke pola Agentic GraphRAG (Plan → Parallel Execute → Synthesize → Reflect) diperlukan — bukan mengganti tech stack, tapi mengubah topology orkestrasi LangGraph.
- **Akar masalah sinkronisasi** = tidak adanya entity resolution terpusat → prioritaskan Fase 1 lebih dulu, dampaknya paling besar untuk effort paling kecil.
- **Akar masalah prompt sensitivity** = router berbasis free-text tanpa structured output dan tanpa eval harness → Fase 0 (eval set) wajib dibuat duluan sebelum ubah apa pun, supaya setiap perubahan terukur.
:::