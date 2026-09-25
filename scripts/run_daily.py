#!/usr/bin/env python3
"""Thin launcher — see nse_scanner.cli.run_daily for the full implementation/docstring.

python scripts/run_daily.py
python scripts/run_daily.py --date 2026-09-10
python scripts/run_daily.py --offline-fixture
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from nse_scanner.cli.run_daily import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
