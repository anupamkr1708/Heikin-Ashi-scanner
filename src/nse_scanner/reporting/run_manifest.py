"""Run manifest (PART 63).

Every run must be reproducible from its manifest alone: code version, resolved configuration,
universe snapshot, data as-of date, and provider versions. Written as JSON next to the Excel
report.
"""

from __future__ import annotations

import json
import platform
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nse_scanner.config import ScannerConfig
from nse_scanner.version import __version__


def _git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:  # noqa: BLE001 - manifest metadata is best-effort, never fatal
        pass
    return None


def _package_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for pkg in ("pandas", "numpy", "yfinance", "duckdb", "pyarrow", "openpyxl", "pydantic", "yaml"):
        try:
            mod = __import__(pkg)
            versions[pkg] = getattr(mod, "__version__", "unknown")
        except ImportError:
            versions[pkg] = "not_installed"
    return versions


@dataclass
class RunManifest:
    run_id: str
    run_timestamp: str
    git_commit: str | None
    python_version: str
    package_versions: dict[str, str]
    universe_id: str
    universe_snapshot_date: str | None
    universe_source: str | None
    constituent_count: int | None
    data_provider: str
    data_as_of: str | None
    expected_session: str | None
    signal_date: str | None
    price_basis: str
    configuration: dict[str, Any]
    research_mode: bool
    status: str
    software_version: str = __version__
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write(self, path: str | Path) -> Path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, default=str)
        return Path(path)


def build_run_manifest(
    run_id: str,
    cfg: ScannerConfig,
    universe_id: str,
    universe_snapshot_date: str | None,
    universe_source: str | None,
    constituent_count: int | None,
    data_provider: str,
    data_as_of: str | None,
    expected_session: str | None,
    signal_date: str | None,
    status: str,
    price_basis: str | None = None,
    extra: dict[str, Any] | None = None,
) -> RunManifest:
    """`price_basis` should be the ACTUAL basis the data provider returned
    (e.g. `ScanRunResult.price_basis`) — NOT blindly copied from `cfg.data.price_basis`, which is
    only a configured default/intent and can silently diverge from reality (the NSE bhavcopy path
    is always RAW regardless of what `cfg.data.price_basis` happens to say). Falls back to the
    config value only when the caller genuinely has no better information (e.g. before any
    provider has run).
    """
    return RunManifest(
        run_id=run_id,
        run_timestamp=datetime.now(timezone.utc).isoformat(),
        git_commit=_git_commit(),
        python_version=platform.python_version(),
        package_versions=_package_versions(),
        universe_id=universe_id,
        universe_snapshot_date=universe_snapshot_date,
        universe_source=universe_source,
        constituent_count=constituent_count,
        data_provider=data_provider,
        data_as_of=data_as_of,
        expected_session=expected_session,
        signal_date=signal_date,
        price_basis=price_basis if price_basis is not None else cfg.data.price_basis,
        configuration=cfg.to_dict(),
        research_mode=cfg.research.run_research_mode,
        status=status,
        extra=extra or {},
    )
