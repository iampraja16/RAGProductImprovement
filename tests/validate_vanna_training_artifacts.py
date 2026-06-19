import os
import sys
import json
import yaml

def validate_artifacts():
    print("=== Validating Vanna Training Artifacts ===")
    
    project_root = os.getcwd()
    artifacts_dir = os.path.join(project_root, "vanna_training")
    
    # 1. Validate schema.sql
    schema_path = os.path.join(artifacts_dir, "schema.sql")
    if not os.path.exists(schema_path):
        print("[-] schema.sql is missing!")
        sys.exit(1)
        
    with open(schema_path, "r", encoding="utf-8") as f:
        schema_content = f.read()
    if not schema_content.strip().startswith("CREATE TABLE"):
        print("[-] schema.sql does not seem to contain a valid CREATE TABLE statement!")
        sys.exit(1)
    print(f"[+] schema.sql is valid. (Size: {len(schema_content)} bytes)")
    
    # 2. Validate qa_pairs.yaml
    qa_path = os.path.join(artifacts_dir, "qa_pairs.yaml")
    if not os.path.exists(qa_path):
        print("[-] qa_pairs.yaml is missing!")
        sys.exit(1)
        
    with open(qa_path, "r", encoding="utf-8") as f:
        try:
            qa_data = yaml.safe_load(f)
        except Exception as e:
            print(f"[-] qa_pairs.yaml is not valid YAML: {e}")
            sys.exit(1)
            
    if not isinstance(qa_data, list):
        print("[-] qa_pairs.yaml root must be a list of QA pairs!")
        sys.exit(1)
        
    for idx, item in enumerate(qa_data):
        if "question" not in item or "sql" not in item:
            print(f"[-] Entry {idx} in qa_pairs.yaml is missing 'question' or 'sql' key!")
            sys.exit(1)
    print(f"[+] qa_pairs.yaml is valid. (Loaded {len(qa_data)} Q&A pairs)")
    
    # 3. Validate domain_docs.md
    doc_path = os.path.join(artifacts_dir, "domain_docs.md")
    if not os.path.exists(doc_path):
        print("[-] domain_docs.md is missing!")
        sys.exit(1)
        
    with open(doc_path, "r", encoding="utf-8") as f:
        doc_content = f.read()
    if not doc_content.strip().startswith("#"):
        print("[-] domain_docs.md does not start with a markdown header (#)!")
        sys.exit(1)
    print(f"[+] domain_docs.md is valid. (Size: {len(doc_content)} bytes)")
    
    # 4. Validate notebook/6_vanna_training.ipynb
    notebook_path = os.path.join(project_root, "notebook", "6_vanna_training.ipynb")
    if not os.path.exists(notebook_path):
        print("[-] 6_vanna_training.ipynb is missing!")
        sys.exit(1)
        
    with open(notebook_path, "r", encoding="utf-8") as f:
        try:
            notebook_json = json.load(f)
        except Exception as e:
            print(f"[-] 6_vanna_training.ipynb is not valid JSON: {e}")
            sys.exit(1)
            
    print(f"[+] 6_vanna_training.ipynb is valid JSON.")
    print("=== All Vanna Training Artifacts Validated Successfully ===")

if __name__ == "__main__":
    validate_artifacts()
