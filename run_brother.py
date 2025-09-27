#!/usr/bin/env python3
"""
Brother工作機械カタログ専用アプリ起動スクリプト
Run Brother machine tool catalog application
"""

import os
import sys
import subprocess
from pathlib import Path

def main():
    """Brother専用アプリを起動"""
    print("🔧 Starting Brother Machine Tool Catalog Application...")
    print("=" * 50)

    # Brother PDFファイルの存在確認
    catalog_dir = Path("CatalogDATA")
    brother_files = [f for f in catalog_dir.glob("*.pdf")
                    if 'brother' in f.name.lower()]

    if not brother_files:
        print("⚠️  Warning: No Brother catalog files found in CatalogDATA/")
        print("   Please add Brother PDF files to CatalogDATA/ directory")
        print()
    else:
        print(f"📄 Found {len(brother_files)} Brother catalog files:")
        for file in brother_files:
            print(f"   • {file.name}")
        print()

    # 環境変数チェック（QA機能用、オプション）
    if not os.getenv('OPENAI_API_KEY'):
        print("💡 Info: OPENAI_API_KEY not set (QA chat will be disabled)")
        print("   To enable QA features, set: export OPENAI_API_KEY='your-key'")
        print()

    # Streamlit起動
    cmd = [
        sys.executable,
        "-m", "streamlit",
        "run",
        "brother_catalog.py",
        "--server.port=8501",
        "--server.address=0.0.0.0",
        "--server.headless=true"
    ]

    print(f"🚀 Launching application...")
    print(f"   Command: {' '.join(cmd)}")
    print(f"   Access at: http://localhost:8501")
    print()

    try:
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        print("\n👋 Application stopped by user")
    except subprocess.CalledProcessError as e:
        print(f"❌ Error running application: {e}")
    except FileNotFoundError:
        print("❌ Error: Streamlit not found. Please install with: pip install streamlit")

if __name__ == "__main__":
    main()