#!/usr/bin/env python3
"""
Machine Tool Catalog Sales Support Application

Main entry point for the Streamlit application that helps sales teams
compare machine tool specifications and query catalog information.
"""

import sys
import os

# Add src to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.catalog_app import main

if __name__ == "__main__":
    main()
