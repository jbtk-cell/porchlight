"""Put src/ on the import path so `pytest` works from the repo root."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
