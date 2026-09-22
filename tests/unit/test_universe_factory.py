from dataclasses import replace

import pytest
from nse_scanner.config import ScannerConfig
from nse_scanner.exceptions import ConfigurationError
from nse_scanner.universe.factory import get_universe_provider
from nse_scanner.universe.mainboard import NSEMainboardEquityUniverseProvider
from nse_scanner.universe.nifty200 import NSENifty200UniverseProvider


def test_nifty200_scope_returns_nifty200_provider():
    cfg = replace(ScannerConfig(), universe=replace(ScannerConfig().universe, universe_scope="NIFTY_200"))
    provider = get_universe_provider(cfg)
    assert isinstance(provider, NSENifty200UniverseProvider)


def test_mainboard_scope_returns_mainboard_provider():
    """The exact bug fixed: run_daily.py used to always build NSENifty200UniverseProvider
    regardless of universe_scope. This must return a DIFFERENT, real provider."""
    cfg = replace(ScannerConfig(), universe=replace(ScannerConfig().universe, universe_scope="NSE_MAINBOARD_EQ"))
    provider = get_universe_provider(cfg)
    assert isinstance(provider, NSEMainboardEquityUniverseProvider)
    assert not isinstance(provider, NSENifty200UniverseProvider)


def test_not_yet_implemented_scope_raises_clear_error():
    cfg = replace(ScannerConfig(), universe=replace(ScannerConfig().universe, universe_scope="NSE_SME"))
    with pytest.raises(ConfigurationError, match="no provider implementation yet"):
        get_universe_provider(cfg)
