"""Universe provider factory.

**Bug fixed here:** `run_daily.py` previously always instantiated `NSENifty200UniverseProvider`
regardless of `cfg.universe.universe_scope`, so setting `NSE_MAINBOARD_EQ` in config had no
effect on which universe actually got scanned. Every script now goes through
`get_universe_provider(cfg)` instead of constructing a provider directly, so there is exactly one
place that maps `universe_scope` to a provider.
"""

from __future__ import annotations

from nse_scanner.config import ScannerConfig
from nse_scanner.data.base import UniverseProvider
from nse_scanner.exceptions import ConfigurationError
from nse_scanner.universe.mainboard import NSEMainboardEquityUniverseProvider
from nse_scanner.universe.nifty200 import NSENifty200UniverseProvider

_NOT_YET_IMPLEMENTED = ("NSE_SME", "NSE_ETF", "NSE_REIT", "NSE_INVIT", "NSE_OTHER")


def get_universe_provider(cfg: ScannerConfig) -> UniverseProvider:
    scope = cfg.universe.universe_scope

    if scope == "NIFTY_200":
        return NSENifty200UniverseProvider(
            min_count=cfg.universe.nifty200_min_count,
            max_count=cfg.universe.nifty200_max_count,
            override_csv_path=cfg.universe.constituent_override_csv_path,
        )

    if scope == "NSE_MAINBOARD_EQ":
        return NSEMainboardEquityUniverseProvider()

    if scope in _NOT_YET_IMPLEMENTED:
        raise ConfigurationError(
            f"universe_scope '{scope}' is a recognized value (config.validate() accepts it) but "
            f"has no provider implementation yet — only NIFTY_200 and NSE_MAINBOARD_EQ are wired "
            f"up in universe/factory.py. Implementing one of these is a matter of adding a "
            f"series-code filter analogous to universe/mainboard.py's EQ/BE filter, not a "
            f"redesign."
        )

    raise ConfigurationError(f"unknown universe_scope: {scope}")
