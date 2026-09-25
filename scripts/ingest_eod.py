#!/usr/bin/env python3
"""Thin launcher — see nse_scanner.cli.ingest_eod for the full implementation/docstring."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from nse_scanner.cli.ingest_eod import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
