import os
import sys
import json
import time
import logging

# Adjust path to import from workspace Cwd
sys.path.append(os.getcwd())

# Suppress verbose warnings
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

from src.agent.agent import Agent
from src.config import settings

def run_evaluation():
    print("=== Starting Golden QA Evaluation Suite ===")
    
    # Load golden QA dataset
    jsonl_path = "eval/golden_qa.jsonl"
    if not os.path.exists(jsonl_path):
        print(f"Error: dataset file not found at {jsonl_path}")
        sys.exit(1)
        
    test_cases = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                test_cases.append(json.loads(line))
                
    print(f"Loaded {len(test_cases)} test cases from {jsonl_path}")
    
    # Initialize Agent
    print("Initializing Agent...")
    agent = Agent()
    
    results = []
    routing_success = 0
    sql_success = 0
    entity_success = 0
    sql_total = 0
    graph_total = 0
    
    total_start_time = time.time()
    
    for idx, tc in enumerate(test_cases, 1):
        question = tc["question"]
        target_tool = tc["target_tool"]
        
        print(f"\n[{idx}/{len(test_cases)}] Evaluating query: '{question}'")
        
        start_time = time.time()
        try:
            resp = agent.get_response(question)
        except Exception as e:
            print(f"Execution failed: {e}")
            resp = {"answer": f"Error: {e}", "sql": None, "steps": []}
            
        latency = time.time() - start_time
        
        # 1. Determine selected tool from steps
        selected_tool = None
        for step in resp.get("steps", []):
            if step.get("node") == "router" and step.get("tool_call"):
                selected_tool = step["tool_call"]["name"]
                break
                
        routing_correct = (selected_tool == target_tool)
        if routing_correct:
            routing_success += 1
            
        # 2. Evaluate tool-specific outputs
        sql_match = False
        entity_matches = []
        entity_ratio = 0.0
        
        if target_tool == "ask_emr_database":
            sql_total += 1
            generated_sql = resp.get("sql")
            expected_keywords = tc.get("expected_sql", [])
            
            if generated_sql:
                # Check if all expected keywords are in generated SQL (case-insensitive)
                matches_all = True
                for kw in expected_keywords:
                    if kw.lower() not in generated_sql.lower():
                        matches_all = False
                        break
                if matches_all:
                    sql_success += 1
                    sql_match = True
            else:
                generated_sql = "[No SQL generated]"
                
            print(f"  Target: SQL | Selected: {selected_tool} | Routing: {'OK' if routing_correct else 'FAIL'}")
            print(f"  Generated SQL: {generated_sql}")
            print(f"  SQL Match: {'OK' if sql_match else 'FAIL'} (Expected: {expected_keywords})")
            
        elif target_tool == "ask_emr_graph":
            graph_total += 1
            answer = resp.get("answer", "")
            expected_entities = tc.get("expected_entities", [])
            
            matched = 0
            for ent in expected_entities:
                if ent.lower() in answer.lower():
                    matched += 1
                    entity_matches.append((ent, True))
                else:
                    entity_matches.append((ent, False))
                    
            if expected_entities:
                entity_ratio = matched / len(expected_entities)
                if matched > 0:
                    entity_success += 1
            else:
                entity_ratio = 1.0
                entity_success += 1
                
            print(f"  Target: Graph | Selected: {selected_tool} | Routing: {'OK' if routing_correct else 'FAIL'}")
            print(f"  Entities matched: {matched}/{len(expected_entities)} ({entity_ratio * 100:.1f}%)")
            
        results.append({
            "index": idx,
            "question": question,
            "target_tool": target_tool,
            "selected_tool": selected_tool,
            "routing_correct": routing_correct,
            "sql_match": sql_match,
            "entity_ratio": entity_ratio,
            "latency_seconds": latency,
            "answer": resp.get("answer"),
            "sql": resp.get("sql")
        })
        
    total_time = time.time() - total_start_time
    
    # Calculate final scores
    routing_accuracy = routing_success / len(test_cases) if test_cases else 0.0
    sql_accuracy = sql_success / sql_total if sql_total else 0.0
    entity_recall = entity_success / graph_total if graph_total else 0.0
    avg_latency = total_time / len(test_cases) if test_cases else 0.0
    
    summary = {
        "total_queries": len(test_cases),
        "routing_accuracy": routing_accuracy,
        "sql_accuracy": sql_accuracy,
        "entity_recall": entity_recall,
        "total_time_seconds": total_time,
        "average_latency_seconds": avg_latency
    }
    
    print("\n" + "="*50)
    print("EVALUATION RUN COMPLETE")
    print("="*50)
    print(f"Total Queries Evaluated: {summary['total_queries']}")
    print(f"Routing Accuracy:        {summary['routing_accuracy'] * 100:.1f}% ({routing_success}/{len(test_cases)})")
    print(f"SQL Generation Accuracy: {summary['sql_accuracy'] * 100:.1f}% ({sql_success}/{sql_total})")
    print(f"Graph Entity Recall:     {summary['entity_recall'] * 100:.1f}% ({entity_success}/{graph_total})")
    print(f"Total Time Taken:        {summary['total_time_seconds']:.2f}s")
    print(f"Average Latency:         {summary['average_latency_seconds']:.2f}s")
    print("="*50)
    
    # Save baseline_results.json
    results_path = "eval/baseline_results.json"
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "detail": results}, f, indent=2)
    print(f"Saved detail results to {results_path}")
    
    # Save baseline_metrics.md
    metrics_path = "eval/baseline_metrics.md"
    with open(metrics_path, "w", encoding="utf-8") as f:
        f.write(f"""# Sprint 2 Baseline Evaluation Metrics

Recorded on: 2026-06-19
Model: qwen2.5:7b (via Ollama)

## Summary Metrics

| Metric | Score | Raw Count |
|:---|:---|:---|
| **Total Test Queries** | {summary['total_queries']} | - |
| **Routing Accuracy** | {summary['routing_accuracy'] * 100:.1f}% | {routing_success}/{len(test_cases)} |
| **SQL Generation Accuracy** | {summary['sql_accuracy'] * 100:.1f}% | {sql_success}/{sql_total} |
| **Graph Entity Recall** | {summary['entity_recall'] * 100:.1f}% | {entity_success}/{graph_total} |
| **Total Time** | {summary['total_time_seconds']:.2f}s | - |
| **Average Latency** | {summary['average_latency_seconds']:.2f}s | - |

## Detailed Results

""")
        for r in results:
            f.write(f"### {r['index']}. {r['question']}\n")
            f.write(f"- **Target Tool**: `{r['target_tool']}`\n")
            f.write(f"- **Selected Tool**: `{r['selected_tool']}` (Routing: {'**PASSED**' if r['routing_correct'] else '**FAILED**'})\n")
            if r['target_tool'] == "ask_emr_database":
                f.write(f"- **SQL Generated**: `{r['sql']}`\n")
                f.write(f"- **SQL Match**: {'**PASSED**' if r['sql_match'] else '**FAILED**'}\n")
            else:
                f.write(f"- **Entity Match Recall**: `{r['entity_ratio'] * 100:.1f}%`\n")
            f.write(f"- **Latency**: `{r['latency_seconds']:.2f}s`\n\n")
            
    print(f"Saved human-readable baseline metrics to {metrics_path}")

if __name__ == "__main__":
    run_evaluation()
