"""Refresh local architecture documents before a frontend build."""

import sys
from pathlib import Path

from main.architecture import scan


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    result = scan(Path(__file__).resolve().parents[1])
    print(f"OMM: {result['files']} source files, {len(result['nodes'])} elements")
