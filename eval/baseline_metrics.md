# Sprint 2 Baseline Evaluation Metrics

Recorded on: 2026-06-19
Model: qwen2.5:7b (via Ollama)

## Summary Metrics

| Metric | Score | Raw Count |
|:---|:---|:---|
| **Total Test Queries** | 30 | - |
| **Routing Accuracy** | 76.7% | 23/30 |
| **SQL Generation Accuracy** | 40.0% | 8/20 |
| **Graph Entity Recall** | 60.0% | 6/10 |
| **Total Time** | 2358.95s | - |
| **Average Latency** | 78.63s | - |

## Detailed Results

### 1. Berapa total fault per model?
- **Target Tool**: `ask_emr_database`
- **Selected Tool**: `ask_emr_database` (Routing: **PASSED**)
- **SQL Generated**: `SELECT COUNT(*) AS total_repairs FROM emr_records LIMIT 500;`
- **SQL Match**: **FAILED**
- **Latency**: `80.95s`

### 2. Model apa yang paling sering rusak?
- **Target Tool**: `ask_emr_database`
- **Selected Tool**: `ask_emr_graph` (Routing: **FAILED**)
- **SQL Generated**: `None`
- **SQL Match**: **FAILED**
- **Latency**: `100.22s`

### 3. Tampilkan 5 site dengan masalah terbanyak
- **Target Tool**: `ask_emr_database`
- **Selected Tool**: `ask_emr_database` (Routing: **PASSED**)
- **SQL Generated**: `SELECT branch_site, COUNT(*) as total_issues FROM emr_records GROUP BY branch_site ORDER BY total_issues DESC LIMIT 5;`
- **SQL Match**: **PASSED**
- **Latency**: `77.37s`

### 4. Tampilkan tren kerusakan per bulan
- **Target Tool**: `ask_emr_database`
- **Selected Tool**: `ask_emr_database` (Routing: **PASSED**)
- **SQL Generated**: `SELECT DATE_TRUNC('month', created_date) AS month, COUNT(*) AS total_cases FROM emr_records GROUP BY month ORDER BY month LIMIT 500;`
- **SQL Match**: **PASSED**
- **Latency**: `95.26s`

### 5. Tampilkan tren masalah pada PC200 per bulan
- **Target Tool**: `ask_emr_database`
- **Selected Tool**: `ask_emr_database` (Routing: **PASSED**)
- **SQL Generated**: `SELECT DATE_TRUNC('month', created_date) as month, COUNT(*) as total FROM emr_records WHERE model_family = 'PC200' GROUP BY month ORDER BY month LIMIT 500;`
- **SQL Match**: **PASSED**
- **Latency**: `96.63s`

### 6. Berapa banyak kasus kerusakan pada model HD785-7 yang memiliki ringkasan GraphRAG?
- **Target Tool**: `ask_emr_database`
- **Selected Tool**: `ask_emr_database` (Routing: **PASSED**)
- **SQL Generated**: `SELECT COUNT(*) FROM emr_records WHERE machine_model = 'HD785-7' AND graph_community_summary IS NOT NULL LIMIT 500;`
- **SQL Match**: **PASSED**
- **Latency**: `66.21s`

### 7. Tampilkan 5 komponen yang paling sering rusak
- **Target Tool**: `ask_emr_database`
- **Selected Tool**: `None` (Routing: **FAILED**)
- **SQL Generated**: `None`
- **SQL Match**: **FAILED**
- **Latency**: `11.89s`

### 8. Berapa total unit yang rusak berdasarkan branch site?
- **Target Tool**: `ask_emr_database`
- **Selected Tool**: `ask_emr_database` (Routing: **PASSED**)
- **SQL Generated**: `SELECT branch_site, COUNT(*) AS total_units FROM emr_records GROUP BY branch_site LIMIT 500;`
- **SQL Match**: **FAILED**
- **Latency**: `91.81s`

### 9. Ada berapa banyak serial number unik yang tercatat?
- **Target Tool**: `ask_emr_database`
- **Selected Tool**: `ask_emr_database` (Routing: **PASSED**)
- **SQL Generated**: `SELECT machine_model, COUNT(*) AS total_faults, CASE WHEN COUNT(*) = ( SELECT COUNT(*) FROM emr_records ) THEN 'Paling Sering' ELSE 'Paling Jarang' END AS status FROM emr_records GROUP BY machine_model ORDER BY total_faults DESC LIMIT 1;`
- **SQL Match**: **FAILED**
- **Latency**: `64.24s`

### 10. Hitung jumlah kasus untuk setiap jenis status
- **Target Tool**: `ask_emr_database`
- **Selected Tool**: `ask_emr_database` (Routing: **PASSED**)
- **SQL Generated**: `SELECT status, COUNT(*) as total FROM emr_records GROUP BY status LIMIT 500;`
- **SQL Match**: **PASSED**
- **Latency**: `63.08s`

### 11. Tampilkan 3 model heavy machinery yang memiliki jumlah kerusakan paling sedikit
- **Target Tool**: `ask_emr_database`
- **Selected Tool**: `ask_emr_database` (Routing: **PASSED**)
- **SQL Generated**: `SELECT machine_model, COUNT(*) as total FROM emr_records GROUP BY machine_model ORDER BY total ASC LIMIT 5;`
- **SQL Match**: **FAILED**
- **Latency**: `61.48s`

### 12. Tampilkan detail data EMR untuk serial number 12345
- **Target Tool**: `ask_emr_database`
- **Selected Tool**: `None` (Routing: **FAILED**)
- **SQL Generated**: `None`
- **SQL Match**: **FAILED**
- **Latency**: `16.63s`

### 13. Berapa banyak kerusakan yang dilaporkan pada tahun 2026?
- **Target Tool**: `ask_emr_database`
- **Selected Tool**: `ask_emr_database` (Routing: **PASSED**)
- **SQL Generated**: `SELECT COUNT(*) FROM emr_records WHERE created_date >= '2026-01-01' AND created_date < '2027-01-01' LIMIT 500;`
- **SQL Match**: **PASSED**
- **Latency**: `53.07s`

### 14. Tampilkan total part supply per machine model
- **Target Tool**: `ask_emr_database`
- **Selected Tool**: `ask_emr_database` (Routing: **PASSED**)
- **SQL Generated**: `None`
- **SQL Match**: **FAILED**
- **Latency**: `70.64s`

### 15. Berapa total part supply yang digunakan oleh model PC200-8?
- **Target Tool**: `ask_emr_database`
- **Selected Tool**: `ask_emr_database` (Routing: **PASSED**)
- **SQL Generated**: `None`
- **SQL Match**: **FAILED**
- **Latency**: `61.12s`

### 16. Tampilkan daftar branch site unik
- **Target Tool**: `ask_emr_database`
- **Selected Tool**: `ask_emr_graph` (Routing: **FAILED**)
- **SQL Generated**: `None`
- **SQL Match**: **FAILED**
- **Latency**: `120.68s`

### 17. Ada berapa kasus EMR yang terdaftar?
- **Target Tool**: `ask_emr_database`
- **Selected Tool**: `ask_emr_database` (Routing: **PASSED**)
- **SQL Generated**: `SELECT COUNT(*) FROM emr_records LIMIT 500;`
- **SQL Match**: **PASSED**
- **Latency**: `51.02s`

### 18. Tampilkan daftar model heavy machinery yang unik
- **Target Tool**: `ask_emr_database`
- **Selected Tool**: `ask_emr_graph` (Routing: **FAILED**)
- **SQL Generated**: `None`
- **SQL Match**: **FAILED**
- **Latency**: `18.76s`

### 19. Tampilkan 5 main cause part no yang paling sering menjadi penyebab masalah
- **Target Tool**: `ask_emr_database`
- **Selected Tool**: `ask_emr_graph` (Routing: **FAILED**)
- **SQL Generated**: `None`
- **SQL Match**: **FAILED**
- **Latency**: `104.06s`

### 20. Berapa kasus yang disebabkan oleh part no 785-12-34567?
- **Target Tool**: `ask_emr_database`
- **Selected Tool**: `ask_emr_database` (Routing: **PASSED**)
- **SQL Generated**: `SELECT COUNT(*) FROM emr_records WHERE main_cause_part_no = '785-12-34567' LIMIT 500;`
- **SQL Match**: **PASSED**
- **Latency**: `55.48s`

### 21. Apa penyebab utama kegagalan pada swing motor?
- **Target Tool**: `ask_emr_graph`
- **Selected Tool**: `ask_emr_graph` (Routing: **PASSED**)
- **Entity Match Recall**: `100.0%`
- **Latency**: `107.49s`

### 22. Bagaimana cara memperbaiki kebocoran oli hidrolik?
- **Target Tool**: `ask_emr_graph`
- **Selected Tool**: `ask_emr_graph` (Routing: **PASSED**)
- **Entity Match Recall**: `0.0%`
- **Latency**: `85.76s`

### 23. Apa hubungan antara final drive dengan float seal?
- **Target Tool**: `ask_emr_graph`
- **Selected Tool**: `ask_emr_graph` (Routing: **PASSED**)
- **Entity Match Recall**: `100.0%`
- **Latency**: `119.22s`

### 24. Tolong jelaskan analisis kegagalan alternator berdasarkan GraphRAG
- **Target Tool**: `ask_emr_graph`
- **Selected Tool**: `None` (Routing: **FAILED**)
- **Entity Match Recall**: `0.0%`
- **Latency**: `15.23s`

### 25. Apa saja gejala kerusakan pada hydraulic pump?
- **Target Tool**: `ask_emr_graph`
- **Selected Tool**: `ask_emr_graph` (Routing: **PASSED**)
- **Entity Match Recall**: `100.0%`
- **Latency**: `100.06s`

### 26. Bagaimana pola tindakan perbaikan untuk masalah engine overheat?
- **Target Tool**: `ask_emr_graph`
- **Selected Tool**: `ask_emr_graph` (Routing: **PASSED**)
- **Entity Match Recall**: `100.0%`
- **Latency**: `157.46s`

### 27. Jelaskan hubungan komponen drive shaft dengan kegagalan universal joint
- **Target Tool**: `ask_emr_graph`
- **Selected Tool**: `ask_emr_graph` (Routing: **PASSED**)
- **Entity Match Recall**: `100.0%`
- **Latency**: `124.04s`

### 28. Apa rekomendasi perbaikan jika terjadi low pressure pada sistem kemudi?
- **Target Tool**: `ask_emr_graph`
- **Selected Tool**: `ask_emr_graph` (Routing: **PASSED**)
- **Entity Match Recall**: `0.0%`
- **Latency**: `131.89s`

### 29. Bagaimana analisis komunitas GraphRAG terkait kebocoran silinder arm?
- **Target Tool**: `ask_emr_graph`
- **Selected Tool**: `ask_emr_graph` (Routing: **PASSED**)
- **Entity Match Recall**: `0.0%`
- **Latency**: `50.55s`

### 30. Jelaskan hubungan antara battery charging warning light dengan kegagalan alternator
- **Target Tool**: `ask_emr_graph`
- **Selected Tool**: `ask_emr_graph` (Routing: **PASSED**)
- **Entity Match Recall**: `50.0%`
- **Latency**: `106.63s`

