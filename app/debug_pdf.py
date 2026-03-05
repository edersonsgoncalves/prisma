import pdfplumber
import sys

arquivo = sys.argv[1] if len(sys.argv) > 1 else "2026_03.pdf"

with pdfplumber.open(arquivo) as pdf:
    for i, p in enumerate(pdf.pages):
        print(f"\n===== PÁGINA {i+1} =====")
        texto = p.extract_text(x_tolerance=3, y_tolerance=3)
        print(texto)