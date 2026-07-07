# Golden QA Evaluation Framework

This framework provides an automated tool to evaluate the routing and response quality of the EMR Fault Analyzer. It tests both quantitative SQL-based queries and qualitative Graph-based queries.

## 1. Directory Structure

```
eval/
├── golden_qa.jsonl      # Test cases (30 records: 20 SQL, 10 Graph)
├── run_eval.py          # Python runner script
├── baseline_results.json # Raw evaluation logs (saved after execution)
└── baseline_metrics.md  # Human-readable markdown metrics (saved after execution)
```

## 2. Test Case Format

Each line in `golden_qa.jsonl` is a JSON record with the following schema:

```json
{
  "question": "Question text here?",
  "target_tool": "ask_emr_database" or "ask_emr_graph",
  "expected_sql": ["list", "of", "sub-strings", "expected", "in", "sql"],
  "expected_entities": ["list", "of", "entities", "expected", "in", "graph", "response"]
}
```

## 3. Running the Evaluation

To execute the test suite and evaluate performance, run the following command from the repository root:

```bash
python eval/run_eval.py
```

### Metrics Generated
1. **Routing Accuracy**: Ratio of test cases routed to the correct database or graph tool.
2. **SQL Generation Accuracy**: Ratio of SQL-targeted queries that successfully generated SQL containing all specified target substrings.
3. **Graph Entity Recall**: Ratio of Graph-targeted queries that returned a response containing at least one of the expected semantic entities.
4. **Latency Metrics**: Tracks individual query duration and calculates average backend response latency.
