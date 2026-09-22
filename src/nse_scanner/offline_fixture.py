"""Offline, no-network scan path used by `--offline-fixture` and by CI/regression tests.

Uses SYNTHETIC data shaped like the documented NSE schema (see
tests/fixtures/synthetic_market_data.py for why it's synthetic rather than real) so the full
pipeline — universe -> data -> features -> signal -> optional context -> Excel/manifest — can be
exercised end-to-end with zero network access. This is what proves there are no broken imports,
no circular dependencies, and no undefined functions in the wiring between modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from nse_scanner.config import ScannerConfig
from nse_scanner.data.base import BenchmarkProvider, DataProvider, UniverseProvider
from nse_scanner.data.calendar import SessionInfo
from nse_scanner.data.storage import MarketDataStore
from nse_scanner.models.market_data import NormalizedBarMeta, PriceBasis
from nse_scanner.pipeline.scan import run_scan
from nse_scanner.reporting.excel import write_report
from nse_scanner.reporting.run_manifest import build_run_manifest


@dataclass
class InMemoryUniverseProvider(UniverseProvider):
    constituents: pd.DataFrame

    def get_constituents(self):
        return self.constituents.copy(), "SYNTHETIC_FIXTURE", datetime.now(timezone.utc).isoformat()


@dataclass
class InMemoryDataProvider(DataProvider):
    histories: dict[str, pd.DataFrame]
    name: str = "SYNTHETIC_FIXTURE"
    price_basis: str = "RAW"

    def fetch_history(self, symbol: str, period: str, interval: str):
        df = self.histories.get(symbol)
        if df is None:
            return None, None, "no synthetic history for this symbol"
        meta = NormalizedBarMeta(isin=None, nse_symbol=symbol, source=self.name,
                                  price_basis=PriceBasis.RAW, interval=interval,
                                  retrieved_at=datetime.now(timezone.utc), provider_version="synthetic")
        return df, meta, None

    def fetch_history_batch(self, symbols: list[str], period: str, interval: str):
        results: dict[str, pd.DataFrame] = {}
        errors: dict[str, str] = {}
        for s in symbols:
            df, _meta, err = self.fetch_history(s, period, interval)
            if df is not None:
                results[s] = df
            else:
                errors[s] = err or "unknown"
        return results, errors


@dataclass
class InMemoryBenchmarkProvider(BenchmarkProvider):
    index_df: pd.DataFrame | None

    def fetch_benchmark(self, ticker: str, period: str, interval: str):
        if self.index_df is None:
            return None, "no synthetic benchmark configured"
        return self.index_df.copy(), None


def run_offline_fixture_scan(cfg: ScannerConfig, session: SessionInfo, store: MarketDataStore) -> int:
    from nse_scanner.testing.synthetic_market_data import (
        SYNTHETIC_SYMBOLS,
        generate_synthetic_index,
        generate_synthetic_ohlcv,
        generate_synthetic_universe,
    )

    print("Generating deterministic SYNTHETIC fixtures (NOT real NSE data) ...")
    universe_df = generate_synthetic_universe()
    histories = {
        sym: generate_synthetic_ohlcv(sym, n_days=80, engineer_breakout_on_last_day=(sym == SYNTHETIC_SYMBOLS[0]))
        for sym in SYNTHETIC_SYMBOLS
    }
    index_df = generate_synthetic_index()

    universe_provider = InMemoryUniverseProvider(universe_df)
    data_provider = InMemoryDataProvider(histories)
    benchmark_provider = InMemoryBenchmarkProvider(index_df)

    # Use the last available synthetic trading day as the expected session so CURRENT/staleness
    # classification behaves sensibly against synthetic (non-calendar-aligned) dates.
    last_date = max(df.index[-1] for df in histories.values())
    synthetic_session = SessionInfo(
        as_of_date=session.as_of_date, expected_completed_session=last_date.date(),
        signal_date=last_date.date(), planned_entry_date=session.planned_entry_date,
    )

    result = run_scan(cfg, universe_provider, data_provider, benchmark_provider, synthetic_session,
                       holidays=set())

    fname = f"NSE_Technical_Scanner_OFFLINE_FIXTURE_{last_date.date().isoformat()}.xlsx"
    report_path = Path(cfg.paths.reports_dir) / fname
    write_report(result.sheets, report_path)
    manifest = build_run_manifest(
        run_id=result.run_id, cfg=cfg, universe_id="SYNTHETIC_FIXTURE",
        universe_snapshot_date=session.as_of_date.isoformat(), universe_source=result.universe_source,
        constituent_count=result.constituent_count, data_provider=data_provider.name,
        data_as_of=last_date.date().isoformat(), expected_session=last_date.date().isoformat(),
        signal_date=last_date.date().isoformat(), status=result.run_health,
        price_basis=result.price_basis,  # was omitted; fell back to cfg default instead of the
                                          # actual basis the (synthetic) provider returned
        # See cli/run_daily.py for why these are here (v1.3 audit finding).
        extra={
            "mode": "OFFLINE_FIXTURE", "note": "synthetic data, not real NSE data",
            "benchmark_status": result.benchmark_status,
            "data_validation_failures": result.data_validation_failures,
            "insufficient_history_count": result.insufficient_history_count,
            "security_scan_failures": result.security_scan_failures,
            "signals_current": result.signals_current,
            "signals_stale": result.signals_stale,
        },
    )
    manifest_path = Path(cfg.paths.reports_dir) / "run_manifest_OFFLINE_FIXTURE.json"
    manifest.write(manifest_path)

    print(f"UNIVERSE:          {result.constituent_count} (synthetic)")
    print(f"BENCHMARK:         {result.benchmark_status}")
    print(f"CURRENT SIGNALS:   {result.signals_current}")
    print(f"RUN_HEALTH:        {result.run_health}")
    print(f"REPORT:            {report_path}")
    print(f"MANIFEST:          {manifest_path}")
    return 0
