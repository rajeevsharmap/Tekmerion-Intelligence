import os
import sys

# backend/*.py import each other with bare module names (e.g. `from
# data_store import DataStore`), so backend/ itself must be on sys.path -
# mirrors how run_pipeline.py etc. are actually invoked (`cd backend &&
# python3 run_pipeline.py`), not a package-relative import scheme.
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)