"""Central, typed configuration model.

Every strategy-changing parameter lives here — nothing is buried inside a function body.
Configuration is loaded from (in increasing priority): built-in defaults -> YAML file ->
environment variables -> explicit CLI overrides. The final *resolved* configuration is what
gets written into every run's ``run_manifest.json`` (PART 63), so a run is reproducible from
its manifest alone.

All optional filters default to False (PART 26 — "ALL OPTIONAL FILTERS DEFAULT FALSE").
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

from nse_scanner.exceptions import ConfigurationError


# ---------------------------------------------------------------------------
# PART A — MANDATORY BASELINE STRATEGY (never silently changed — see strategy/bb_ha.py)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BaselineStrategyConfig:
    bb_period: int = 20
    bb_std_mult: float = 2.0
    bb_ddof: int = 1                 # 1 = sample std (pandas default), 0 = population std
    max_bb_overshoot_pct: float = 4.0
    min_ha_body_pct: float = 1.0
    strategy_id: str = "bb_ha_v1_base"


# ---------------------------------------------------------------------------
# PART B — HISTORY TIERS (fixes the flat MIN_ROWS=220 bug — PART 16 / BUG 6)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class HistoryTierConfig:
    atr_period: int = 14
    min_rows_primary: int = 30       # covers BB(20) + ATR(14) + small buffer
    min_rows_sma20: int = 20
    min_rows_sma50: int = 50
    min_rows_sma200: int = 200
    min_rows_rs_20d: int = 21
    min_rows_rs_60d: int = 61
    min_rows_rs_120d: int = 121


# ---------------------------------------------------------------------------
# PART C — OPTIONAL CONFIRMATION FILTERS (ALL default False)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class OptionalFilterConfig:
    use_trend_filter: bool = False              # Close > SMA50
    use_long_trend_filter: bool = False          # Close > SMA200
    use_volume_filter: bool = False              # Volume_Ratio_20 > 1.0
    use_bandwidth_filter: bool = False           # BB width expanding vs. yesterday
    use_atr_filter: bool = False                 # BB_Overshoot_ATR within band
    use_candle_quality_filter: bool = False      # CLV >= threshold
    use_relative_strength_filter: bool = False   # RS_20D > 0
    use_market_regime_filter: bool = False       # benchmark regime == BULL
    use_fresh_breakout_only: bool = False        # Breakout_Type == FRESH_BREAKOUT

    atr_overshoot_max: float = 1.5
    candle_quality_min_clv: float = 0.5


# ---------------------------------------------------------------------------
# PART D — DATA / PROVIDER PARAMETERS
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DataConfig:
    interval: str = "1d"
    yfinance_auto_adjust: bool = True
    yfinance_repair: bool = False  # yfinance's repair logic pulls in optional dependencies
                                     # (scipy for one repair type, scikit-learn for another) that
                                     # only surface as ModuleNotFoundError the first time repair
                                     # actually finds something to fix — which can happen mid-run,
                                     # symbol by symbol, rather than failing fast at install time.
                                     # Off by default so a fresh install just works; both packages
                                     # are still in pyproject.toml if you want to turn this back on.
    yfinance_keepna: bool = False
    yfinance_group_by: str = "ticker"
    yfinance_threads: bool = True
    yfinance_version_tested: str = "1.7.0"  # bumped from 0.2.40 after a real user hit
                                              # yfinance 0.2.40 being rejected by Yahoo's current
                                              # API (JSONDecodeError on every request) — this
                                              # field is a recorded/reported value for the run
                                              # manifest, not an enforced pin (see pyproject.toml)
    price_basis: str = "ADJUSTED"        # ADJUSTED | RAW — must be explicit, never mixed
    max_allowed_data_lag_sessions: int = 1
    batch_size: int = 50
    batch_pause_sec: float = 1.0
    max_download_retries: int = 2
    retry_backoff_sec: float = 3.0
    cache_enabled: bool = True
    cache_max_age_hours: int = 20


# ---------------------------------------------------------------------------
# PART E — UNIVERSE
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class UniverseConfig:
    universe_scope: str = "NIFTY_200"    # NIFTY_200 | NSE_MAINBOARD_EQ | ...
    nifty200_min_count: int = 150
    nifty200_max_count: int = 210
    constituent_override_csv_path: str | None = None


# ---------------------------------------------------------------------------
# PART F — BENCHMARK
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BenchmarkConfig:
    index_ticker: str = "^NSEI"
    index_name: str = "NIFTY 50"
    secondary_index_ticker: str = "^CNX200"
    secondary_index_name: str = "NIFTY 200"


# ---------------------------------------------------------------------------
# PART G — RESEARCH
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ResearchConfig:
    run_research_mode: bool = False
    entry_price_method: str = "next_open"     # next_open | next_close
    forward_return_horizons: tuple[int, ...] = (1, 3, 5, 10, 20, 40)
    primary_research_horizon: int = 5
    independent_event_cooldown_days: int = 5   # cooldown after a FRESH_BREAKOUT before the next
                                                # signal day can count as a new independent event
    walk_forward_train_years: float = 3.0
    walk_forward_test_months: float = 6.0
    live_history_period: str = "2y"
    research_history_start: str = "2018-01-01"


# ---------------------------------------------------------------------------
# PART H — COST MODEL (broker/cost profile — PART 50)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CostProfileConfig:
    name: str = "illustrative_default"
    brokerage_buy_pct: float = 0.03
    brokerage_sell_pct: float = 0.03
    stt_buy_pct: float = 0.0
    stt_sell_pct: float = 0.10
    exchange_buy_pct: float = 0.0035
    exchange_sell_pct: float = 0.0035
    gst_pct: float = 0.18            # applied on (brokerage + exchange), not on trade value
    stamp_duty_buy_pct: float = 0.015
    stamp_duty_sell_pct: float = 0.0
    slippage_buy_pct: float = 0.05
    slippage_sell_pct: float = 0.05
    dp_fee_flat: float = 0.0

    def round_trip_cost_pct(self) -> float:
        buy_side = (self.brokerage_buy_pct + self.exchange_buy_pct + self.slippage_buy_pct
                    + self.stamp_duty_buy_pct)
        sell_side = (self.brokerage_sell_pct + self.exchange_sell_pct + self.slippage_sell_pct
                     + self.stamp_duty_sell_pct)
        gst = (self.brokerage_buy_pct + self.exchange_buy_pct
               + self.brokerage_sell_pct + self.exchange_sell_pct) * self.gst_pct
        return buy_side + sell_side + gst + self.stt_buy_pct + self.stt_sell_pct


# ---------------------------------------------------------------------------
# PART I — RISK / PORTFOLIO (PART 35)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade_pct: float = 1.0
    max_concurrent_positions: int = 10
    max_sector_exposure_pct: float = 30.0
    max_portfolio_exposure_pct: float = 100.0
    min_position_size: int = 1
    max_position_size: int | None = None


# ---------------------------------------------------------------------------
# PART J — STORAGE / PATHS
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PathsConfig:
    data_dir: str = "data"
    raw_dir: str = "data/raw"
    processed_dir: str = "data/processed"
    cache_dir: str = "data/cache"
    reports_dir: str = "reports"
    logs_dir: str = "logs"
    duckdb_path: str = "data/processed/nse_scanner.duckdb"


# ---------------------------------------------------------------------------
# TOP-LEVEL CONFIG
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ScannerConfig:
    baseline: BaselineStrategyConfig = field(default_factory=BaselineStrategyConfig)
    history: HistoryTierConfig = field(default_factory=HistoryTierConfig)
    filters: OptionalFilterConfig = field(default_factory=OptionalFilterConfig)
    data: DataConfig = field(default_factory=DataConfig)
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    benchmark: BenchmarkConfig = field(default_factory=BenchmarkConfig)
    research: ResearchConfig = field(default_factory=ResearchConfig)
    cost_profile: CostProfileConfig = field(default_factory=CostProfileConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> None:
        if not (0 < self.baseline.max_bb_overshoot_pct):
            raise ConfigurationError("max_bb_overshoot_pct must be > 0")
        if self.baseline.bb_ddof not in (0, 1):
            raise ConfigurationError("bb_ddof must be 0 or 1")
        if self.research.entry_price_method not in ("next_open", "next_close"):
            raise ConfigurationError("entry_price_method must be 'next_open' or 'next_close'")
        if self.data.price_basis not in ("ADJUSTED", "RAW"):
            raise ConfigurationError("price_basis must be 'ADJUSTED' or 'RAW'")
        if self.universe.universe_scope not in (
            "NIFTY_200", "NSE_MAINBOARD_EQ", "NSE_SME", "NSE_ETF", "NSE_REIT", "NSE_INVIT", "NSE_OTHER",
        ):
            raise ConfigurationError(f"unknown universe_scope: {self.universe.universe_scope}")


def _merge_dataclass(instance: Any, overrides: dict[str, Any]) -> Any:
    cls = type(instance)
    kwargs = {f.name: getattr(instance, f.name) for f in fields(cls)}
    for key, val in overrides.items():
        if key not in kwargs:
            raise ConfigurationError(f"unknown configuration key '{key}' for {cls.__name__}")
        kwargs[key] = val
    return cls(**kwargs)


_SECTION_MAP = {
    "baseline": "baseline",
    "history": "history",
    "filters": "filters",
    "data": "data",
    "universe": "universe",
    "benchmark": "benchmark",
    "research": "research",
    "cost_profile": "cost_profile",
    "risk": "risk",
    "paths": "paths",
}


def load_config(yaml_path: str | Path | None = None, cli_overrides: dict[str, Any] | None = None) -> ScannerConfig:
    """Resolve configuration: defaults -> YAML -> environment -> CLI overrides.

    Environment variables are read as ``NSE_SCANNER__<SECTION>__<FIELD>`` (double underscore
    separated), e.g. ``NSE_SCANNER__RESEARCH__RUN_RESEARCH_MODE=true``.
    """
    cfg = ScannerConfig()

    if yaml_path is not None and Path(yaml_path).exists():
        with open(yaml_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        cfg = _apply_overrides(cfg, raw)

    env_overrides: dict[str, dict[str, Any]] = {}
    prefix = "NSE_SCANNER__"
    for env_key, env_val in os.environ.items():
        if not env_key.startswith(prefix):
            continue
        parts = env_key[len(prefix):].lower().split("__")
        if len(parts) != 2:
            continue
        section, field_name = parts
        env_overrides.setdefault(section, {})[field_name] = _coerce_env_value(env_val)
    cfg = _apply_overrides(cfg, env_overrides)

    if cli_overrides:
        cfg = _apply_overrides(cfg, cli_overrides)

    cfg.validate()
    return cfg


def _coerce_env_value(val: str) -> Any:
    low = val.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        if "." in val:
            return float(val)
        return int(val)
    except ValueError:
        return val


def _apply_overrides(cfg: ScannerConfig, raw: dict[str, Any]) -> ScannerConfig:
    kwargs: dict[str, Any] = {}
    for section, section_overrides in raw.items():
        if section not in _SECTION_MAP:
            raise ConfigurationError(f"unknown configuration section '{section}'")
        current = getattr(cfg, _SECTION_MAP[section])
        kwargs[_SECTION_MAP[section]] = _merge_dataclass(current, section_overrides)
    if not kwargs:
        return cfg
    return _merge_dataclass(cfg, kwargs)
