"""Test full PDF generation pipeline (without LLM)."""
import sys
sys.path.insert(0, ".")

from src.executive_summary import generate_summary

print("Generating PDF for PC200 (no LLM)...")
pdf_bytes, html_content = generate_summary("PC200", use_llm=False)

print(f"HTML length: {len(html_content)} chars")
print(f"PDF size: {len(pdf_bytes)} bytes ({len(pdf_bytes)//1024} KB)")

# Save for manual inspection
with open("output_test.pdf", "wb") as f:
    f.write(pdf_bytes)
print("Saved to output_test.pdf")

with open("output_test.html", "w", encoding="utf-8") as f:
    f.write(html_content)
print("Saved to output_test.html")

print("[OK] Full pipeline test passed!")
