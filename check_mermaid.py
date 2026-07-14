import re
with open(r'D:\PEMROGRAMAN\LLM-DESKTOP\local-rag-comparator\README.md', 'r', encoding='utf-8') as f:
    content = f.read()
matches = re.findall(r'```mermaid.*?```', content, re.DOTALL)
print(f'README.md: {len(matches)} mermaid blocks')
for i, m in enumerate(matches):
    print(f'  Block {i+1}: OK ({len(m)} chars)')