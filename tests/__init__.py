"""Make the project importable when tests run as a package."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "backend", ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
