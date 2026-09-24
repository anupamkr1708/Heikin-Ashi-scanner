"""Exception hierarchy for nse_scanner.

Every raised exception in this package is one of these types (or a subclass). Nothing in this
codebase uses a bare ``except: pass`` — every failure path is either handled explicitly and
logged, or re-raised. See PART 40 / PART 61 of the project specification.
"""

from __future__ import annotations


class NseScannerError(Exception):
    """Base class for all errors raised intentionally by this package."""


class UniverseIntegrityError(NseScannerError):
    """Raised when a requested universe (e.g. NIFTY 200, NSE_MAINBOARD_EQ) cannot be validated.

    This is a hard-fail condition by design (PART 6 of the spec). It must never be silently
    caught and replaced with a small fallback list. Callers that want a resilient batch job
    should catch this at the top level, log it, and abort the run — not substitute data.
    """


class DataProviderError(NseScannerError):
    """Raised by a DataProvider implementation on an unrecoverable fetch/parse failure."""


class DataValidationError(NseScannerError):
    """Raised when OHLCV data fails schema or sanity validation."""


class FrameNormalizationError(DataValidationError):
    """Raised by data/normalization.py when a raw provider dataframe (yfinance or otherwise)
    cannot be normalized to the canonical OHLCV shape — unrecognized MultiIndex orientation,
    a requested ticker missing from a multi-ticker frame, missing required fields after
    normalization, or no usable rows remaining. Always carries a specific, actionable message
    (which level/values were seen) rather than a bare KeyError — see PART 59: no error may be
    vague about what failed, where, or what was expected vs observed."""


class SymbolMappingError(NseScannerError):
    """Raised when a security cannot be mapped between identity systems (ISIN/NSE/Yahoo)."""


class StorageError(NseScannerError):
    """Raised on a DuckDB/Parquet storage-layer failure."""


class ConfigurationError(NseScannerError):
    """Raised when the resolved configuration is invalid or internally inconsistent."""


class CalendarError(NseScannerError):
    """Raised on an NSE trading-calendar/session resolution failure."""


class SignalMathError(NseScannerError):
    """Raised when an indicator/signal sanity assertion fails (PART 67 gates)."""
