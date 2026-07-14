# Dokumentasi Fitur: Graph Extraction Pipeline

## Apa yang Dilakukan Fitur Ini?

Fitur ini tugasnya **ngubah data CSV mentah jadi struktur graf di Neo4j**.

Prosesnya: baca data EMR dari file CSV → kirim ke AI untuk diekstrak entity dan relasinya → simpan hasilnya ke Neo4j.

Contoh: satu baris CSV:
```
emr_name: U-00000158, model: PC200-10M0, symptom: ENGINE OVERHEAT, component: ENGINE
```

Nah, dari satu baris ini, AI bakal bikin node dan relasi di Neo4j:
```
[MachineModel: PC200-10M0] --(HAS_SYMPTOM)--> [SymptomPattern: ENGINE OVERHEAT]
[EMRRecord: U-00000158] --(HAS_COMPONENT)--> [Component: ENGINE]
[EMRRecord: U-00000158] --(HAS_MODEL)--> [MachineModel: PC200-10M0]
```

## Alur Kerja (Flowchart)

::: mermaid
graph TD
    A[Dashboard EMR.csv
20.630 baris data] --> B[Loop:
proses per batch 500]
    
    B --> C[LLM Extraction
Kirim ke AI
Azure OpenAI atau OpenAI
buat deteksi entitas]
    
    C --> D[AI ekstrak:
- Symptom, Component
- Model, Root Cause
- Action, Part Number]
    
    D --> E[Validasi Format
Pastikan output AI
sesuai skema]
    
    E --> F[BATCH MERGE ke Neo4j
500 record sekaligus
pake query UNWIND]
    
    F --> G[Graph Enricher:
tambah relasi
IN_COMMUNITY ke
Community nodes]
    
    G --> H[Neo4j Graph Database
Siap dipake!]
```

## Input → Proses → Output

### Input
File CSV: `data/Dashboard EMR.csv` — 20.630 baris data perawatan alat berat.

### Proses

**Langkah 1 — Baca CSV**
Data dibaca dalam batch 500 baris biar gak boros memory.

**Langkah 2 — AI Extraction**
Setiap batch dikirim ke LLM (Azure OpenAI atau OpenAI biasa) untuk diekstrak:

| Yang Diekstrak | Contoh |
|---------------|--------|
| Symptom | ENGINE OVERHEAT, OIL LEAK |
| Component | ENGINE, FINAL DRIVE, TRANSMISSION |
| Root Cause | KONTAMINASI, AUS, MALFUNCTION |
| Action | OVERHAUL, REPLACE, REPAIR |
| Model | PC200-10M0, HD785-7 |
| Part | SEAL, INJECTOR, ORING |

**Langkah 3 — Validasi**
Output AI dicek: apakah formatnya sesuai? Kalau ada yang error, dilewatin aja (jangan sampe ngebreak seluruh pipeline).

**Langkah 4 — Batch MERGE ke Neo4j**
Data yang valid di-MERGE ke Neo4j dalam batch 500 record pake query `UNWIND`. MERGE itu artinya: kalau node udah ada, update aja. Kalau belum ada, buat baru.

**Langkah 5 — Graph Enricher**
Tambahin relasi `IN_COMMUNITY` yang nyambungin EMRRecord ke Community node.

### Output
Graph Neo4j yang berisi:
- ~20.630 node `EMRRecord`
- Ribuan node `SymptomPattern`, `Component`, `MachineModel`, `RootCausePattern`, `ActionPattern`
- Relasi antar node: `HAS_SYMPTOM`, `HAS_COMPONENT`, `HAS_MODEL`, `MENTIONS`, dll

## Kode Contoh (Simplified)

```python
# File: src/ingestion/extractor.py

class GraphExtractor:
    def extract_and_ingest(self, csv_path: str, batch_size: int = 500) -> None:
        df = pd.read_csv(csv_path)
        for i in range(0, len(df), batch_size):
            chunk = df.iloc[i : i + batch_size]
            extracted = self.llm_client.extract(chunk)  # pake AI
            self.graph_client.write_batch(extracted)     # MERGE ke Neo4j
```

## Catatan Penting untuk Pengembang Selanjutnya

1. **Pipeline ini PELAN.** Proses 20.630 record butuh 2-3 jam karena tergantung kecepatan AI (LLM). Sabar ya kalau jalanin.

2. **Butuh checkpoint.** Kalau listrik mati atau error di tengah jalan, jangan khawatir — ada mekanisme checkpoint biar bisa lanjut dari batch terakhir, bukan dari awal.

3. **Support Azure OpenAI dan OpenAI biasa.** Di config, kamu bisa pilih provider. Yang penting API key-nya bener.

4. **AI kadang ngasih output yang aneh.** Validasi di langkah 3 itu penting banget buat nyaring hasil yang gak sesuai format.

5. **CSV header harus sesuai.** Pastikan kolom di CSV sesuai dengan yang diharapkan extractor. Biasanya: `EMR`, `Unit`, `Model`, `Component`, `Symptom`, `SMR`, `Date`, dll.

6. **Batch size 500 itu optimal.** Lebih gede dari itu risiko timeout atau error koneksi ke Neo4j.
:::