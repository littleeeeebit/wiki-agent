"""`python tool/translate --check` and the rest of the translator's CLI."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from translate import main  # noqa: E402

raise SystemExit(main())
