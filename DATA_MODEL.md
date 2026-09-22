# Data model

Core entities (PART 75), with ISIN treated as the principal security identity wherever it is
actually available (see "Known gap" below).

## `security_master` (`models/security.py::SecurityRecord`)

| Field | Notes |
|---|---|
| `isin` | Canonical identity. `"UNKNOWN:<symbol>"` placeholder when a source doesn't supply one — never fabricated. |
| `nse_symbol` | Current NSE trading symbol. |
| `company_name`, `series`, `security_code`, `status`, `active`, `sector` | Descriptive. |
| `date_of_listing`, `date_of_deactivation` | Lifecycle. |
| `yf_symbol`, `historical_symbol_aliases`, `symbol_valid_from`, `symbol_valid_to` | Yahoo-side mapping, versioned. |
| `mapping_status/method/confidence/error` | Audit trail for the ISIN→NSE→Yahoo mapping (PART 4). |
| `source`, `source_version`, `retrieved_at`, `updated_at` | Provenance. |

## `eod_prices` (`data/storage.py::EOD_PRICES_SCHEMA`, Parquet + DuckDB)

`nse_symbol, isin, trade_date, open, high, low, close, volume, turnover, source, price_basis,
schema_version, ingested_at` — long format, one row per security per session. De-duplicated on
`(nse_symbol, trade_date, source)`, last-write-wins (a re-ingested session overwrites, never
doubles up).

## `universe_snapshot` (`universe/validation.py::UniverseSnapshot`)

`universe_id, snapshot_date, source, source_version, retrieved_at, constituent_count,
symbol_count, duplicate_count, invalid_count, validation_status`.

## `corporate_actions` (`data/corporate_actions.py::CorporateActionRecord`)

`isin, nse_symbol, action_type, ex_date, ratio_or_amount, source, retrieved_at`. See "Known gap"
below — this repository has the schema and the gap-flagging logic, but no live CA feed wired up.

## `scan_runs` / `run_manifest` (`reporting/run_manifest.py::RunManifest`)

`run_id, run_timestamp, git_commit, python_version, package_versions, universe_id,
universe_snapshot_date, universe_source, constituent_count, data_provider, data_as_of,
expected_session, signal_date, price_basis, configuration (the FULL resolved ScannerConfig),
research_mode, status`.

## `signals` (one row per `Live_Signals`/`Diagnostics` entry, `pipeline/scan.py`)

`Rank, Signal_Date, Stock, Company_Name, Sector, CMP, BB_Middle/Upper/Lower/StdDev/Width_Pct/PctB,
BB_Overshoot_Pct, HA_Open/Close/Body_Pct, ATR14, ATR_Pct, BB_Overshoot_ATR, Volume,
Volume_Ratio_20, Dollar_Volume, SMA20/50/200, Dist_SMA50_Pct, Dist_SMA200_Pct, RS_20D/60D/120D,
Breakout_Type, Days_Above_Upper_BB, Market_Regime, Data_Status, History_Quality,
Benchmark_Status, Score_Status, Research_Heuristic_Score, Optional_Filters_Passed, Signal_Reason`.

## `research_events` (`research/events.py`, `cli/run_research.py` output)

Per independent event: `Symbol, Signal_Date, Entry_Date, Entry_Price, Gap_Pct, Breakout_Type,
FwdRet_1D/3D/5D/10D/20D/40D, MFE_Pct, MAE_Pct`.

## Price basis

Every OHLCV frame is tagged `RAW` or `ADJUSTED` (`models/market_data.py::PriceBasis`) and this is
carried through `config.DataConfig.price_basis`, provenance records, and the run manifest.
`data/storage.py` stores NSE bhavcopy data as `RAW` (official EOD prices are not
split/dividend-adjusted). yfinance data defaults to `ADJUSTED` (`auto_adjust=True`). **These are
never mixed within one calculation** — every indicator function receives a single DataFrame with
one price basis, and the run manifest records which one was used.

## Known gap: ISIN coverage

NSE's public index-constituent CSVs are commonly understood to include an ISIN column, but this
repository's build sandbox could not fetch a live file to confirm the current column name (see
README "Known limitations"). `universe/security_master.py::build_security_master` reads ISIN from
the constituents frame if present and falls back to the `"UNKNOWN:<symbol>"` placeholder
otherwise, rather than fabricating a value or silently using the NSE symbol as if it were the
canonical identity.
