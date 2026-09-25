"""NSE symbol -> Yahoo Finance symbol mapping (PART 4).

The override table lives in a versioned config file (`config/symbol_overrides.yaml`), not as a
four-line dict buried in code — PART 4 explicitly forbids "a four-symbol manual override list as
the architecture". Every mapping produced here carries Mapping_Status / Mapping_Method /
Mapping_Confidence / Mapping_Error so downstream consumers can audit it (see universe_audit sheet
in reporting/excel.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from nse_scanner.models.security import MappingStatus

YFINANCE_SUFFIX = ".NS"


@dataclass(frozen=True)
class MappingResult:
    yf_symbol: str
    method: str
    confidence: float
    status: str
    error: str | None = None


def load_override_table(path: str | Path) -> dict[str, str]:
    p = Path(path)
    if not p.exists():
        return {}
    with open(p, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return dict(raw.get("overrides", {}))


def map_symbol_to_yfinance(nse_symbol: str, override_table: dict[str, str]) -> MappingResult:
    if nse_symbol in override_table:
        base = override_table[nse_symbol]
        return MappingResult(
            yf_symbol=f"{base}{YFINANCE_SUFFIX}",
            method="override_table",
            confidence=1.0,
            status=MappingStatus.OVERRIDE,
        )
    return MappingResult(
        yf_symbol=f"{nse_symbol}{YFINANCE_SUFFIX}",
        method="direct_suffix",
        confidence=0.9,
        status=MappingStatus.PENDING,
    )


def finalize_mapping_status(mapping: MappingResult, download_succeeded: bool) -> MappingResult:
    """Called once download/validation results are known, mirroring the legacy notebook's
    two-phase (pending -> resolved) mapping status pattern."""
    if download_succeeded:
        return MappingResult(mapping.yf_symbol, mapping.method, mapping.confidence, MappingStatus.OK)
    return MappingResult(
        mapping.yf_symbol,
        mapping.method,
        mapping.confidence,
        MappingStatus.ERROR,
        error="no data returned for mapped yfinance symbol",
    )
