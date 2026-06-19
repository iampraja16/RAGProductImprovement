import os
import sys
import json
import time
import logging
import concurrent.futures
import requests

# Adjust path to import from workspace Cwd
sys.path.append(os.getcwd())

# Suppress verbose warnings
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

from src.agent.agent import Agent
from src.config import settings

def check_ollama_heartbeat(base_url: str) -> bool:
    """Perform pre-flight HTTP check to ensure Ollama service is running."""
    try:
        r = requests.get(base_url, timeout=5)
        return r.status_code == 200
    except Exception as e:
        logger.warning(f"Ollama heartbeat check failed: {e}")
        return False

def save_atomic_json(filepath: str, data: dict):
    """Save data to filepath atomically using a temporary file and rename/replace."""
    temp_filepath = filepath + ".tmp"
    try:
        with open(temp_filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(temp_filepath, filepath)
    except Exception as e:
        logger.error(f"Failed to save atomic JSON to {filepath}: {e}")
        if os.path.exists(temp_filepath):
            try:
                os.remove(temp_filepath)
            except Exception:
                pass

def save_atomic_text(filepath: str, text: str):
    """Save text to filepath atomically using a temporary file and rename/replace."""
    temp_filepath = filepath + ".tmp"
    try:
        with open(temp_filepath, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(temp_filepath, filepath)
    except Exception as e:
        logger.error(f"Failed to save atomic text to {filepath}: {e}")
        if os.path.exists(temp_filepath):
            try:
                os.remove(temp_filepath)
            except Exception:
                pass

def run_evaluation():
    print("=== Starting Golden QA Evaluation Suite ===")
    
    # 1. Pre-flight Ollama heartbeat check
    print("Performing pre-flight Ollama heartbeat check...")
    if not check_ollama_heartbeat(settings.ollama_base_url):
        print(f"Error: Ollama service is not running or unreachable at {settings.ollama_base_url}")
        sys.exit(1)
    print("Ollama service heartbeat OK.")
    
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
    
    # 2. Resume support from partial_results.json
    partial_path = "eval/partial_results.json"
    results = []
    completed_indices = set()
    
    if os.path.exists(partial_path):
        try:
            with open(partial_path, "r", encoding="utf-8") as f:
                partial_data = json.load(f)
                results = partial_data.get("results", [])
                completed_indices = {r["index"] for r in results}
                print(f"Found partial results checkpoint. Resuming from query index {len(completed_indices) + 1}")
        except Exception as e:
            print(f"Warning: Failed to load partial results ({e}). Starting fresh.")
            results = []
            completed_indices = set()
            
    # Initialize Agent
    print("Initializing Agent...")
    agent = Agent()
    
    consecutive_failures = 0
    max_consecutive_failures = 3
    max_retries = 3  # up to 3 retries (4 attempts total)
    timeout_seconds = 60
    
    total_start_time = time.time()
    
    # 3. Evaluation Loop with Timeout, Retries, and Fail-fast
    for idx, tc in enumerate(test_cases, 1):
        if idx in completed_indices:
            continue
            
        question = tc["question"]
        target_tool = tc["target_tool"]
        
        print(f"\n[{idx}/{len(test_cases)}] Evaluating query: '{question}'")
        
        resp = None
        success = False
        latency = 0.0
        
        for attempt in range(1, max_retries + 2):
            start_time = time.time()
            if attempt > 1:
                print(f"  Attempt {attempt}/{max_retries + 1} (Retry)...")
                
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(agent.get_response, question)
                    resp = future.result(timeout=timeout_seconds)
                success = True
                latency = time.time() - start_time
                break  # Exit retry loop on success
            except concurrent.futures.TimeoutError:
                latency = time.time() - start_time
                print(f"  Attempt {attempt} timed out after {timeout_seconds} seconds.")
            except Exception as e:
                latency = time.time() - start_time
                print(f"  Attempt {attempt} failed with exception: {e}")
                
            if attempt < max_retries + 1:
                time.sleep(2)  # Brief backoff before retry
                
        if not success:
            consecutive_failures += 1
            print(f"  Query failed after {max_retries + 1} attempts.")
            resp = {"answer": "Error: Timeout or execution failure after retries", "sql": None, "steps": []}
            
            # Fail-fast check
            if consecutive_failures >= max_consecutive_failures:
                print(f"\nAborting evaluation: {max_consecutive_failures} consecutive queries failed.")
                save_atomic_json(partial_path, {"results": results})
                sys.exit(1)
        else:
            consecutive_failures = 0
            
        # Determine selected tool
        selected_tool = None
        for step in resp.get("steps", []):
            if step.get("node") == "router" and step.get("tool_call"):
                selected_tool = step["tool_call"]["name"]
                break
                
        routing_correct = (selected_tool == target_tool)
        
        # Evaluate tool-specific outputs
        sql_match = False
        entity_ratio = 0.0
        sql_success_increment = False
        entity_success_increment = False
        
        if target_tool == "ask_emr_database":
            generated_sql = resp.get("sql")
            expected_keywords = tc.get("expected_sql", [])
            
            if generated_sql:
                matches_all = True
                for kw in expected_keywords:
                    if kw.lower() not in generated_sql.lower():
                        matches_all = False
                        break
                if matches_all:
                    sql_success_increment = True
                    sql_match = True
            else:
                generated_sql = "[No SQL generated]"
                
            print(f"  Target: SQL | Selected: {selected_tool} | Routing: {'OK' if routing_correct else 'FAIL'}")
            print(f"  Generated SQL: {generated_sql}")
            print(f"  SQL Match: {'OK' if sql_match else 'FAIL'} (Expected: {expected_keywords})")
            
        elif target_tool == "ask_emr_graph":
            answer = resp.get("answer", "")
            expected_entities = tc.get("expected_entities", [])
            
            matched = 0
            for ent in expected_entities:
                if ent.lower() in answer.lower():
                    matched += 1
                    
            if expected_entities:
                entity_ratio = matched / len(expected_entities)
                if matched > 0:
                    entity_success_increment = True
            else:
                entity_ratio = 1.0
                entity_success_increment = True
                
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
            "sql": resp.get("sql"),
            "sql_success_increment": sql_success_increment,
            "entity_success_increment": entity_success_increment
        })
        
        # Save atomic checkpoint after each completed question
        save_atomic_json(partial_path, {"results": results})
        
    total_time = time.time() - total_start_time
    
    # Calculate final scores
    routing_success = sum(1 for r in results if r["routing_correct"])
    sql_total = sum(1 for r in results if r["target_tool"] == "ask_emr_database")
    sql_success = sum(1 for r in results if r["target_tool"] == "ask_emr_database" and r["sql_success_increment"])
    graph_total = sum(1 for r in results if r["target_tool"] == "ask_emr_graph")
    entity_success = sum(1 for r in results if r["target_tool"] == "ask_emr_graph" and r["entity_success_increment"])
    
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
    save_atomic_json(results_path, {"summary": summary, "detail": results})
    print(f"Saved detail results to {results_path}")
    
    # Save baseline_metrics.md
    metrics_path = "eval/baseline_metrics.md"
    markdown_content = f"""# Sprint 2 Baseline Evaluation Metrics

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

"""
    for r in results:
        markdown_content += f"### {r['index']}. {r['question']}\n"
        markdown_content += f"- **Target Tool**: `{r['target_tool']}`\n"
        markdown_content += f"- **Selected Tool**: `{r['selected_tool']}` (Routing: {'**PASSED**' if r['routing_correct'] else '**FAILED**'})\n"
        if r['target_tool'] == "ask_emr_database":
            markdown_content += f"- **SQL Generated**: `{r['sql']}`\n"
            markdown_content += f"- **SQL Match**: {'**PASSED**' if r['sql_match'] else '**FAILED**'}\n"
        else:
            markdown_content += f"- **Entity Match Recall**: `{r['entity_ratio'] * 100:.1f}%`\n"
        markdown_content += f"- **Latency**: `{r['latency_seconds']:.2f}s`\n\n"
        
    save_atomic_text(metrics_path, markdown_content)
    print(f"Saved human-readable baseline metrics to {metrics_path}")
    
    # Clean up partial results checkpoint file on successful completion
    if os.path.exists(partial_path):
        try:
            os.remove(partial_path)
            print("Cleaned up partial results checkpoint.")
        except Exception as e:
            print(f"Warning: Failed to delete partial results checkpoint: {e}")

if __name__ == "__main__":
    run_evaluation()
