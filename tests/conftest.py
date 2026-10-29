import sys
from pathlib import Path

# Чтобы тесты могли импортировать tests/_load.py
sys.path.insert(0, str(Path(__file__).resolve().parent))
