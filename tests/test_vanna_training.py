import unittest
import os
import json
import yaml

class TestVannaTrainingArtifacts(unittest.TestCase):
    
    @classmethod
    def setUpClass(cls):
        # Always run relative to workspace root
        cls.project_root = os.getcwd()
        cls.artifacts_dir = os.path.join(cls.project_root, "vanna_training")
        
    def test_schema_sql_validity(self):
        schema_path = os.path.join(self.artifacts_dir, "schema.sql")
        self.assertTrue(os.path.exists(schema_path), "schema.sql is missing!")
            
        with open(schema_path, "r", encoding="utf-8") as f:
            schema_content = f.read()
            
        self.assertTrue(
            schema_content.strip().startswith("CREATE TABLE"),
            "schema.sql does not seem to contain a valid CREATE TABLE statement!"
        )
        
    def test_qa_pairs_yaml_validity(self):
        qa_path = os.path.join(self.artifacts_dir, "qa_pairs.yaml")
        self.assertTrue(os.path.exists(qa_path), "qa_pairs.yaml is missing!")
            
        with open(qa_path, "r", encoding="utf-8") as f:
            qa_data = yaml.safe_load(f)
                
        self.assertIsInstance(qa_data, list, "qa_pairs.yaml root must be a list of QA pairs!")
            
        for idx, item in enumerate(qa_data):
            self.assertIn("question", item, f"Entry {idx} in qa_pairs.yaml is missing 'question' key!")
            self.assertIn("sql", item, f"Entry {idx} in qa_pairs.yaml is missing 'sql' key!")
            
    def test_domain_docs_markdown_validity(self):
        doc_path = os.path.join(self.artifacts_dir, "domain_docs.md")
        self.assertTrue(os.path.exists(doc_path), "domain_docs.md is missing!")
            
        with open(doc_path, "r", encoding="utf-8") as f:
            doc_content = f.read()
            
        self.assertTrue(
            doc_content.strip().startswith("#"),
            "domain_docs.md does not start with a markdown header (#)!"
        )
        
    def test_notebook_json_validity(self):
        notebook_path = os.path.join(self.project_root, "notebook", "6_vanna_training.ipynb")
        self.assertTrue(os.path.exists(notebook_path), "6_vanna_training.ipynb is missing!")
            
        with open(notebook_path, "r", encoding="utf-8") as f:
            try:
                notebook_json = json.load(f)
            except Exception as e:
                self.fail(f"6_vanna_training.ipynb is not valid JSON: {e}")
                
        self.assertIsInstance(notebook_json, dict)
        self.assertIn("cells", notebook_json)

if __name__ == "__main__":
    unittest.main()
