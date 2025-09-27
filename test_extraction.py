#!/usr/bin/env python3
"""
Test script for PDF extraction functionality
"""

import sys
import os
from pathlib import Path

# Add src to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.pdf_extractor import load_and_extract_pdfs
from src.vector_search import create_dataframe_from_specs

def test_extraction():
    """Test PDF extraction"""
    print("🔍 Testing PDF extraction...")

    catalog_dir = Path("CatalogDATA")

    # Check if PDFs exist
    pdf_files = list(catalog_dir.glob("*.pdf"))
    print(f"Found {len(pdf_files)} PDF files:")
    for pdf in pdf_files:
        print(f"  • {pdf.name}")

    if not pdf_files:
        print("❌ No PDF files found in CatalogDATA/")
        return

    try:
        # Extract specifications
        specs_list = load_and_extract_pdfs(catalog_dir)

        print(f"\n✅ Extracted {len(specs_list)} machine specifications")

        if specs_list:
            # Show first few specs
            for i, spec in enumerate(specs_list[:3]):
                print(f"\n--- Spec {i+1}: {spec.metadata.brand} {spec.metadata.model} ---")
                print(f"Series: {spec.metadata.series}")
                print(f"Source: {spec.metadata.doc_source}")
                print(f"X/Y/Z: {spec.axis_travel.X_mm}/{spec.axis_travel.Y_mm}/{spec.axis_travel.Z_mm}")
                print(f"Spindle max: {spec.spindle.max_rpm}")
                print(f"Tool count: {spec.tool_mag.tools}")
                if spec.notes:
                    print(f"Notes: {', '.join(spec.notes[:3])}")

            # Test DataFrame conversion
            df = create_dataframe_from_specs(specs_list)
            print(f"\n📊 Created DataFrame with {len(df)} rows and {len(df.columns)} columns")
            print(f"Columns: {list(df.columns)}")

            # Show basic stats
            print(f"\nBrands: {df['brand'].unique().tolist()}")
            print(f"Series: {df['series'].unique().tolist()}")

        else:
            print("❌ No specifications extracted")

    except Exception as e:
        print(f"❌ Error during extraction: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_extraction()