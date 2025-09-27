#!/usr/bin/env python3
"""
Brother専用カタログアプリのテストスクリプト
Test Brother catalog application
"""

from pathlib import Path
import sys
import os

# パス設定
sys.path.insert(0, os.path.dirname(__file__))

from brother_catalog import BrotherCatalogExtractor, BrotherSpec

def test_brother_extraction():
    """Brother仕様抽出テスト"""
    print("🔍 Testing Brother catalog extraction...")
    print("=" * 50)

    catalog_dir = Path("CatalogDATA")
    extractor = BrotherCatalogExtractor()

    # Brother PDFファイル検索
    brother_files = [f for f in catalog_dir.glob("*.pdf")
                    if 'brother' in f.name.lower()]

    if not brother_files:
        print("❌ No Brother PDF files found")
        return

    print(f"📄 Found {len(brother_files)} Brother files:")
    for file in brother_files:
        print(f"   • {file.name}")

    # 各ファイルから抽出テスト
    all_specs = []
    for pdf_path in brother_files[:3]:  # 最初の3ファイルをテスト
        print(f"\n--- Processing {pdf_path.name} ---")

        specs = extractor.extract_from_pdf(pdf_path)
        print(f"Extracted {len(specs)} specifications")

        for spec in specs[:2]:  # 最初の2つを表示
            print(f"\n{spec.model} ({spec.series}):")
            print(f"  X/Y/Z: {spec.x_axis}/{spec.y_axis}/{spec.z_axis}")
            if spec.z_option:
                print(f"  Z Option: {spec.z_option}")
            print(f"  Spindle: {spec.spindle_max}")
            if spec.spindle_options:
                print(f"  Spindle Options: {spec.spindle_options}")
            print(f"  Rapid: {spec.rapid_xy}/{spec.rapid_z}")
            print(f"  Tools: {spec.tools}")
            print(f"  5-Axis: {spec.has_5axis}")
            print(f"  100 Tools: {spec.has_100tools}")
            if spec.notes:
                print(f"  Notes: {', '.join(spec.notes)}")

        all_specs.extend(specs)

    print(f"\n✅ Total extracted: {len(all_specs)} specifications")

    # 統計表示
    series_count = {}
    for spec in all_specs:
        series_count[spec.series] = series_count.get(spec.series, 0) + 1

    print(f"\n📊 Series distribution:")
    for series, count in series_count.items():
        print(f"   • {series}: {count} models")

    # 仕様データの完全性チェック
    complete_specs = [s for s in all_specs if s.x_axis and s.y_axis and s.z_axis]
    print(f"\n📈 Data completeness:")
    print(f"   • Complete axis data: {len(complete_specs)}/{len(all_specs)} models")

    spindle_specs = [s for s in all_specs if s.spindle_max]
    print(f"   • Spindle data: {len(spindle_specs)}/{len(all_specs)} models")

    tool_specs = [s for s in all_specs if s.tools]
    print(f"   • Tool data: {len(tool_specs)}/{len(all_specs)} models")

if __name__ == "__main__":
    test_brother_extraction()