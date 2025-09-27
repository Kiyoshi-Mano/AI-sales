#!/usr/bin/env python3
"""
Debug script to examine PDF text structure
"""

import sys
import os
from pathlib import Path
import pymupdf as fitz
import unicodedata

def debug_pdf(pdf_name: str, max_pages: int = 3):
    """Debug PDF text extraction"""
    catalog_dir = Path("CatalogDATA")
    pdf_path = catalog_dir / pdf_name

    if not pdf_path.exists():
        print(f"❌ PDF not found: {pdf_path}")
        return

    print(f"📄 Debugging: {pdf_name}")
    print("=" * 50)

    doc = fitz.open(str(pdf_path))

    for page_num in range(min(len(doc), max_pages)):
        print(f"\n--- Page {page_num + 1} ---")
        page = doc[page_num]
        text = page.get_text()
        normalized_text = unicodedata.normalize('NFKC', text)

        # Show first 2000 characters
        print(normalized_text[:2000])
        print("\n" + "." * 30)

    doc.close()

def main():
    if len(sys.argv) > 1:
        pdf_name = sys.argv[1]
    else:
        # Default to Brother Xd2 catalog
        pdf_name = "Brother_S300Xd2_S500Xd2_S700Xd2_カタログ.pdf"

    debug_pdf(pdf_name)

if __name__ == "__main__":
    main()