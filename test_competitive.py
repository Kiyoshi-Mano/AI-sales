#!/usr/bin/env python3
"""
Brother vs FANUC競合比較機能のテストスクリプト
Test Brother vs FANUC competitive analysis functionality
"""

from pathlib import Path
import sys
import os

# パス設定
sys.path.insert(0, os.path.dirname(__file__))

from brother_catalog import BrotherCatalogExtractor, FanucCatalogExtractor, MachineSpec

def test_competitive_analysis():
    """Brother vs FANUC競合比較テスト"""
    print("🔍 Testing Brother vs FANUC Competitive Analysis...")
    print("=" * 60)

    catalog_dir = Path("CatalogDATA")

    # Brother抽出
    brother_extractor = BrotherCatalogExtractor()
    brother_files = [f for f in catalog_dir.glob("*.pdf")
                    if 'brother' in f.name.lower()]

    print(f"📄 Found {len(brother_files)} Brother files:")
    for file in brother_files:
        print(f"   • {file.name}")

    all_brother_specs = []
    for pdf_path in brother_files[:2]:  # テスト用に2ファイルのみ
        print(f"\n--- Processing Brother: {pdf_path.name} ---")
        specs = brother_extractor.extract_from_pdf(pdf_path)
        all_brother_specs.extend(specs)
        for spec in specs[:1]:  # 最初の1つだけ表示
            print(f"{spec.brand} {spec.model} ({spec.series})")
            print(f"  X/Y/Z: {spec.x_axis}/{spec.y_axis}/{spec.z_axis}")
            print(f"  Spindle: {spec.spindle_max} rpm")
            print(f"  Tools: {spec.tools}")

    # FANUC抽出
    fanuc_extractor = FanucCatalogExtractor()
    fanuc_files = [f for f in catalog_dir.glob("*.pdf")
                  if 'fanac' in f.name.lower()]

    print(f"\n📄 Found {len(fanuc_files)} FANUC files:")
    for file in fanuc_files:
        print(f"   • {file.name}")

    all_fanuc_specs = []
    for pdf_path in fanuc_files:
        print(f"\n--- Processing FANUC: {pdf_path.name} ---")
        specs = fanuc_extractor.extract_from_pdf(pdf_path)
        all_fanuc_specs.extend(specs)
        for spec in specs[:1]:  # 最初の1つだけ表示
            print(f"{spec.brand} {spec.model} ({spec.series})")
            print(f"  X/Y/Z: {spec.x_axis}/{spec.y_axis}/{spec.z_axis}")
            print(f"  Spindle: {spec.spindle_max} rpm")
            print(f"  Tools: {spec.tools}")
            print(f"  CNC: {spec.cnc_controller}")
            if spec.notes:
                print(f"  Notes: {', '.join(spec.notes[:3])}")

    print(f"\n✅ Extraction Summary:")
    print(f"   • Brother specs: {len(all_brother_specs)}")
    print(f"   • FANUC specs: {len(all_fanuc_specs)}")
    print(f"   • Total: {len(all_brother_specs) + len(all_fanuc_specs)}")

    # 競合比較分析
    print(f"\n📊 Competitive Analysis:")

    # Y軸ストローク比較
    brother_y = [s.y_axis for s in all_brother_specs if s.y_axis]
    fanuc_y = [s.y_axis for s in all_fanuc_specs if s.y_axis]

    if brother_y and fanuc_y:
        print(f"   • Y軸ストローク:")
        print(f"     Brother: {min(brother_y):.0f} - {max(brother_y):.0f} mm")
        print(f"     FANUC:   {min(fanuc_y):.0f} - {max(fanuc_y):.0f} mm")

    # 主軸回転数比較
    brother_spindle = [s.spindle_max for s in all_brother_specs if s.spindle_max]
    fanuc_spindle = [s.spindle_max for s in all_fanuc_specs if s.spindle_max]

    if brother_spindle and fanuc_spindle:
        print(f"   • 主軸最大回転数:")
        print(f"     Brother: {min(brother_spindle):,.0f} - {max(brother_spindle):,.0f} rpm")
        print(f"     FANUC:   {min(fanuc_spindle):,.0f} - {max(fanuc_spindle):,.0f} rpm")

    # 工具本数比較
    brother_tools = [max(s.tools) for s in all_brother_specs if s.tools]
    fanuc_tools = [max(s.tools) for s in all_fanuc_specs if s.tools]

    if brother_tools and fanuc_tools:
        print(f"   • 最大工具本数:")
        print(f"     Brother: {max(brother_tools)} 本")
        print(f"     FANUC:   {max(fanuc_tools)} 本")

    # 特徴機能比較
    brother_5axis = sum(1 for s in all_brother_specs if s.has_5axis)
    fanuc_5axis = sum(1 for s in all_fanuc_specs if s.has_5axis)

    print(f"   • 同時5軸対応:")
    print(f"     Brother: {brother_5axis}/{len(all_brother_specs)} models")
    print(f"     FANUC:   {fanuc_5axis}/{len(all_fanuc_specs)} models")

    # Brother優位点
    print(f"\n🔵 Brother優位点:")
    if brother_y and fanuc_y and max(brother_y) > max(fanuc_y):
        print(f"   ✅ Y軸最大ストローク: {max(brother_y):.0f}mm > {max(fanuc_y):.0f}mm")

    brother_100t = sum(1 for s in all_brother_specs if s.has_100tools)
    if brother_100t > 0:
        print(f"   ✅ 100本マガジン対応: {brother_100t} models")

    # FANUC優位点
    print(f"\n🟠 FANUC優位点:")
    fanuc_rapid_z = [s.rapid_z for s in all_fanuc_specs if s.rapid_z]
    brother_rapid_z = [s.rapid_z for s in all_brother_specs if s.rapid_z]

    if fanuc_rapid_z and brother_rapid_z and max(fanuc_rapid_z) > max(brother_rapid_z):
        print(f"   ✅ Z軸早送り最高速度: {max(fanuc_rapid_z):.0f}m/min > {max(brother_rapid_z):.0f}m/min")

    fanuc_thermal = sum(1 for s in all_fanuc_specs if any('熱変位補正' in note for note in s.notes))
    if fanuc_thermal > 0:
        print(f"   ✅ 熱変位補正機能搭載: {fanuc_thermal} models")

if __name__ == "__main__":
    test_competitive_analysis()