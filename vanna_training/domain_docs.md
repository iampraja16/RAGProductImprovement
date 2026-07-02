# EMR Database Vanna Domain Documentation

The `emr_records` table contains Equipment Maintenance Records for heavy machinery.

## Community-Based Problem Search (PRIMARY method for counting)

- `community_id` is a TEXT[] (array) column containing GraphRAG Leiden community IDs.
  A single community groups all semantically related problem variants together
  (e.g., "engine overheat", "high coolant temp", "thermostat stuck" are in the same community).

  DO NOT add ANY(community_id) conditions to your SQL queries. The system
  automatically injects the correct community_id filter at runtime based on
  entity resolution. Focus only on brand, model, and aggregation patterns.
  If the user does NOT mention a specific problem/symptom, do NOT add any
  community_id or symptom filter — aggregate across ALL records.

  IMPORTANT: When the user asks about a specific problem (e.g., "engine overheat"),
  ALWAYS include a fallback text filter using symptom ILIKE in the SQL.
  This ensures correct results even if community injection fails.
  Example: WHERE symptom ILIKE '%overheat%'

  Untuk query listing komprehensif (bukan agregasi) — misal user tanya "engine overheat terjadi di unit model apa saja dan nomor emr berapa" —
  gunakan SELECT dengan kolom yang diminta (machine_model, emr_name) + WHERE symptom ILIKE filter.
  Jangan gunakan COUNT/GROUP BY untuk query listing. Contoh:
  - "engine overheat terjadi di unit model apa saja dan nomor emr berapa?"
    → SELECT machine_model, emr_name FROM emr_records WHERE symptom ILIKE '%overheat%' ORDER BY machine_model, emr_name;

## Model Filtering Rules

- `machine_model` is the SPECIFIC equipment model with suffix (e.g., 'HD785-7', 'PC200-10M0', 'D155A-6'). 
  ALWAYS use this column when the user mentions a model name with a dash and number suffix like HD785-7 or PC200-8.
- `model_family` is the BROAD category WITHOUT suffix (e.g., 'HD785', 'PC200', 'D155A'). 
  Only use this column when the user asks about a general family like "semua model PC200" without specifying the exact variant.
- `branch_site` is the location where the equipment is operated. When user mentions a site/location name, use ILIKE.
- `account_account_name` is the customer/company name (e.g., 'PAMAPERSADA NUSANTARA', 'PETROSEA Tbk.').
  When user mentions a company/account name, the system resolves abbreviations to full names
  (e.g., 'PAMA' → 'PAMAPERSADA NUSANTARA'). Use exact match (=) for known account names.
  For raw user-entered partial names, use ILIKE as fallback.
- Use `created_date` when filtering by month or year.

## Categorical Columns (use = for filtering)

- `techcare_component`, `techcare_sub_component`, `model_family`, `machine_model`, `sub_call_type`, `pmact_type` are CATEGORICAL columns.
  Use = (equals) for these columns.
  Example: WHERE techcare_component = 'FINAL DRIVE'
  Example: WHERE sub_call_type = 'ENGINE REPAIR'
  Example: WHERE pmact_type = 'CORRECTIVE'

  Note: When aggregating by `techcare_component`, ALWAYS exclude NULL rows:
  `WHERE techcare_component IS NOT NULL`
  Records with NULL component (~34% of data) have no meaningful category assignment.

  Note: `sub_call_type` contains the type/category of service call (e.g., 'ENGINE REPAIR', 'HYDRAULIC', 'TRANSMISSION').
  Use this column when aggregating by problem category — NOT `subjects` (which is free-text).
  Note: `pmact_type` contains the maintenance action type (e.g., 'CORRECTIVE', 'PREVENTIVE', 'INSPECTION').

## Text Columns (only for detail retrieval, NOT for counting/aggregation)

- `symptom`, `caused_of_problem`, `action_how_was_problem_corrected`, `subjects` are FREE TEXT columns.
  Use ILIKE only when the user asks to display/list specific record details.
  DO NOT use ILIKE for aggregation/counting queries.

  CRITICAL: Do NOT use `subjects` in GROUP BY for aggregation like COUNT.
  `subjects` contains individual job descriptions (e.g., "Reinforce track frame J23008"),
  not problem categories. For problem categorization, use `techcare_component`,
  `sub_call_type`, or `pmact_type` instead.

## Generic Problem Descriptions

- When the user mentions a GENERIC problem description (not a specific symptom name like "overheat",
  "bocor", "oli"), you may use ILIKE on symptom/caused_of_problem/subjects to search for
  relevant keywords. Examples:
  - "masalah engine" → ILIKE '%engine%' (NOT 'overheat')
  - "masalah mesin" → ILIKE '%engine%' OR ILIKE '%mesin%'
  - "masalah kelistrikan" → ILIKE '%electrical%' OR ILIKE '%kelistrikan%'

  IMPORTANT: Do NOT map a generic phrase like "masalah engine" to a specific symptom
  like "overheat". Use the actual keywords from the query.

## Service Meter Reading (SMR)

- `smr_trouble` is a NUMERIC column storing the equipment's service meter reading (operating hours) at the time the problem was reported.
- This is critical for SMR-based failure analysis: "pada SMR berapa masalah X muncul".
- Use ORDER BY smr_trouble when returning SMR data for visualization.
- Common analysis patterns:
  - SMR distribution for a specific symptom: `SELECT smr_trouble, emr_name, created_date FROM emr_records WHERE ... ORDER BY smr_trouble`
  - Average SMR for a problem: `SELECT AVG(smr_trouble) FROM emr_records WHERE ...`
  - SMR range: `SELECT MIN(smr_trouble), MAX(smr_trouble), AVG(smr_trouble) FROM emr_records WHERE ...`
- For SMR analysis queries, DO NOT use LIMIT — the user needs ALL data points for visualization.
  The system handles this automatically in the SMR analysis tool.

## Site / Branch Location Mapping

- `branch_site` stores SHORT CODES (3-4 uppercase letters), not full location names.
  Example values: `JBY`, `SMD`, `BIN`, `TJR`, `MLW`.
- Use the `site_reference` table to translate full location names to codes:
  ```sql
  SELECT * FROM site_reference
  ```
  This table has columns: `code` (VARCHAR), `full_name` (VARCHAR).
  Examples: code `JBY` = 'Jembayan', `SMD` = 'Samarinda', `BIN` = 'Binungan'.

- When a user mentions a site by its full name (e.g., "Jembayan", "Samarinda", "Balikpapan"),
  JOIN with `site_reference` to match the correct code:
  - Correct: `JOIN site_reference sr ON e.branch_site = sr.code WHERE sr.full_name ILIKE '%jembayan%'`
  - Correct: `WHERE e.branch_site = 'JBY'` (if user used the code directly)
  - Wrong: `WHERE e.branch_site ILIKE '%Jembayan%'` (code != full name)

- Examples:
  - "masalah sering di site Jembayan" → `JOIN site_reference sr ON e.branch_site = sr.code WHERE LOWER(sr.full_name) = 'jembayan'`
  - "top 5 kerusakan di Balikpapan" → `JOIN site_reference sr ON e.branch_site = sr.code WHERE LOWER(sr.full_name) = 'balikpapan'`
  - "emr dari site JBY" → `WHERE e.branch_site = 'JBY'`
  - "bandingkan site Samarinda dan Binungan" → `WHERE e.branch_site IN ('SMD', 'BIN')`

## Manufacturer / Brand Filtering

- `machine_product` is the MANUFACTURER/BRAND code. It uses SHORT CODES, not full names:
    - `KOMAT` = Komatsu
    - `SCNIA` = Scania
    - `TDANH` = Tadano
    - `NSSAN` = Nissan
    - `BOMAG` = BOMAG
- When a user mentions a brand name (e.g., "Komatsu", "Scania"), ALWAYS use the corresponding short code with `=`, NOT ILIKE:
    - Correct: `WHERE machine_product = 'KOMAT'`
    - Wrong: `WHERE machine_product ILIKE '%Komatsu%'`
- When filtering by brand AND model simultaneously, use BOTH `machine_product` and `machine_model`/`model_family`:
    - Example: `WHERE machine_product = 'KOMAT' AND machine_model ILIKE 'PC200%'`
- Do NOT use ILIKE on `machine_product` — the column only contains short codes.

## Model Filtering Rules

- If the query mentions a specific model name with variant suffix (e.g., 'PC200-10M0', 'HD785-7'), use: `machine_model = '...'`
- If the query mentions a model family without suffix (e.g., 'PC200', 'HD785', 'D155A'), use: `model_family = '...'`
- If the query mentions a PARTIAL model code that could match multiple variants (e.g., user says "PC200" but data has 'PC200-10M0', 'PC2000-11R', 'PC200-8M0'), use ILIKE on `machine_model`:
    - Correct: `WHERE machine_model ILIKE 'PC200%'`
    - Wrong: `WHERE model_family = 'PC200'` (misses PC2000 variants)
- The system handles problem-specific filtering automatically. Do not add community_id conditions.
- If the query does NOT mention any machine model, DO NOT add any model filter — aggregate across ALL models.

## Account / Customer Mapping

- `account_account_name` stores the customer/company name (e.g., 'PAMAPERSADA NUSANTARA', 'PETROSEA Tbk.').
- Use the `account_reference` table to look up valid account names:
  ```sql
  SELECT * FROM account_reference
  ```
- When a user mentions a company/account name (including abbreviations like 'PAMA' for 'PAMAPERSADA NUSANTARA',
  'ADARO' for 'ADARO INDONESIA', 'FREEPORT' for 'FREEPORT INDONESIA'), use exact match:
  - Correct: `WHERE account_account_name = 'PAMAPERSADA NUSANTARA'`
  - Wrong: `WHERE account_account_name ILIKE '%PAMA%'`
- For partial/fuzzy matches provided by the user directly, use ILIKE:
  - Correct: `WHERE account_account_name ILIKE '%PAMA%'`
- Examples:
  - "Problem apa yang sering terjadi di PAMA?" → `WHERE account_account_name = 'PAMAPERSADA NUSANTARA'`
  - "Masalah apa saja di account ADARO?" → `WHERE account_account_name = 'ADARO INDONESIA' OR account_account_name = 'ADARO LOGISTICS'`
  - "Total EMR di FREEPORT" → `WHERE account_account_name = 'FREEPORT INDONESIA'`

## Product Problem Information (PPI)

- `ppi_external_id` is the PPI identifier (e.g., 'PPI.000004', 'PPI.000017'). Use ILIKE for partial matching.
- `ppi_improvement_name` is the PPI title (e.g., 'Techcare.PPI.000004').
- `ppi_phenomenon` describes the problem phenomenon in free text.
- `ppi_corrective_action` describes the recommended corrective action.
- Only ~2.7% of EMR records have PPI data. Always LEFT JOIN or use nullable checks.
- Common PPI queries:
  - "tampilkan emr yang punya PPI" → `WHERE ppi_external_id IS NOT NULL`
  - "PPI apa saja yang ada" → `SELECT DISTINCT ppi_external_id, ppi_improvement_name FROM emr_records WHERE ppi_external_id IS NOT NULL`
  - "cari PPI PPI.000004" → `WHERE ppi_external_id = 'PPI.000004'`
  - "tampilkan EMR dengan PPI tertentu" → `JOIN` or `WHERE ppi_external_id = '...'`
