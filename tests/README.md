# Pengujian Sistem (Testing Suite)

Struktur pengujian di *repository* ini telah dirancang ulang agar terfokus, terisolasi per fitur (Sliced per feature), dan tidak ada redudansi. Seluruh *junk files* sisa pengembangan telah dihapus.

## Struktur Pengujian

Berikut adalah kategori skenario pengujian yang tersedia:

### 1. `test_api_endpoints.py`
Menguji *routing*, *endpoint* FastAPI, dan keamanan autentikasi.
- Menguji API tanpa *header* (harus 403 Forbidden).
- Menguji endpoint publik seperti `/health` dan `/cache/stats` (harus 200 OK).
- Menguji *response* dari *chat endpoint* menggunakan *mock data*.

### 2. `test_agent_tools.py`
Menguji fungsionalitas dan keamanan *Agent* (LLM Tools).
- **SQL Sandbox:** Memastikan fungsi `_is_safe_select_query` memblokir perintah berbahaya seperti `DROP`, `DELETE`, `INSERT` (Anti SQL Injection), namun tetap mengizinkan `SELECT`.
- **Provenance:** Memastikan prompt memaksa LLM untuk menyertakan pembatas `--- EVIDENCE/PROVENANCE ---` dan menyisipkan ID data mentah (*Record Provenance*).

### 3. `test_resilience_circuit.py`
Menguji infrastruktur, keandalan koneksi, dan sistem *Failover*.
- **Circuit Breaker:** Memastikan status berubah dari `CLOSED` -> `OPEN` -> `HALF_OPEN` saat terjadi masalah *network* (misalnya Neo4j *down*).
- **Concurrency:** Menguji *thread-safety* pada pemanggilan konkuren (20 threads) terhadap *Circuit Breaker*.
- **Provider Caching:** Memastikan koneksi ke Vanna/Database berjalan dengan sistem *cache*, dan jika terjadi *connection reset*, koneksi dapat pulih secara *graceful*.

### 4. `test_data_pipeline.py`
Menguji *pipeline* sinkronisasi dari Neo4j (Graph) ke PostgreSQL (SQL).
- **Idempotensi:** Memastikan *script* sinkronisasi aman dijalankan berkali-kali tanpa merusak/menggandakan data di PostgreSQL.
- **Dry-run:** Memastikan mode `--dry-run` benar-benar tidak menyentuh/mengubah data.
- **Rollback:** Memastikan jika transaksi gagal di pertengahan proses, tidak ada tabel *temporary* yang tertinggal dan seluruh transaksi dibatalkan (*Rollback*).

### 5. `test_eval_utils.py`
Menguji alat bantu evaluasi.
- Memastikan fungsi `save_atomic_json` dan `save_atomic_text` bekerja dengan benar (tidak ada file rusak/korup jika aplikasi mati mendadak saat menulis file JSON metrik).

### 6. `test_vanna_training.py`
Menguji keutuhan file pelatihan *Text-to-SQL* (Vanna).
- Memastikan `schema.sql`, `qa_pairs.yaml`, dan `domain_docs.md` berformat benar dan tidak kosong, karena akan berakibat fatal jika agen dilatih menggunakan file yang rusak.

---

## Cara Menjalankan Pengujian

Anda dapat menjalankan seluruh *test suite* sekaligus menggunakan perintah standar Python:

```bash
# Pastikan Anda berada di root direktori project
python -m unittest discover -s tests
```

Atau untuk menjalankan spesifik pada satu kategori fitur:
```bash
python -m unittest tests.test_agent_tools
```
