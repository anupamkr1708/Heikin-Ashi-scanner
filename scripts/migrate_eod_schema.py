from __future__ import annotations

import os
import shutil
from pathlib import Path

import pandas as pd
from nse_scanner.data.storage import _normalize_eod_schema


def migrate_partition(path: Path) -> None:
    print(f"Migrating {path}")

    original = pd.read_parquet(path)
    normalized = _normalize_eod_schema(original)

    if len(normalized) != len(original):
        raise RuntimeError(f"Row count changed for {path}: " f"{len(original)} -> {len(normalized)}")

    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)

    tmp = path.with_suffix(path.suffix + ".tmp")

    normalized.to_parquet(
        tmp,
        index=False,
    )

    # Read back and verify the rewritten file.
    rewritten = pd.read_parquet(tmp)

    if len(rewritten) != len(original):
        raise RuntimeError(f"Post-write row count changed for {path}")

    os.replace(tmp, path)

    print(f"  OK: {len(rewritten)} rows")


def main() -> None:
    root = Path("data/processed/eod_prices")

    for path in sorted(root.glob("year=*.parquet")):
        migrate_partition(path)


if __name__ == "__main__":
    main()
