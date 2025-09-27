#!/usr/bin/env python3
"""
Startup script for the Machine Tool Catalog Sales Support Application

This script runs the Streamlit application.
Usage: python run_app.py
"""

import os
import sys
import subprocess
from pathlib import Path

def main():
    """Run the Streamlit application"""
    # Set environment variables if needed
    os.environ.setdefault('PYTHONPATH', str(Path(__file__).parent / 'src'))

    # Check if OpenAI API key is set
    if not os.getenv('OPENAI_API_KEY'):
        print("⚠️  Warning: OPENAI_API_KEY environment variable not set.")
        print("   Please set it before running the application:")
        print("   export OPENAI_API_KEY='your-api-key-here'")
        print()

    # Run Streamlit
    cmd = [
        sys.executable,
        "-m", "streamlit",
        "run",
        "src/catalog_app.py",
        "--server.port=8501",
        "--server.address=0.0.0.0"
    ]

    print(f"🚀 Starting Machine Tool Catalog Application...")
    print(f"   Command: {' '.join(cmd)}")
    print(f"   Access at: http://localhost:8501")
    print()

    try:
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        print("\n👋 Application stopped by user")
    except subprocess.CalledProcessError as e:
        print(f"❌ Error running application: {e}")

if __name__ == "__main__":
    main()