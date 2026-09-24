"""`python tool/main` — the pipelines live beside this folder, so `tool/` goes on the path."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main.app import main  # noqa: E402

raise SystemExit(main())
