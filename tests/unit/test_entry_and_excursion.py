import pandas as pd
from nse_scanner.research.forward_returns import NEXT_CLOSE, NEXT_OPEN, forward_return, resolve_entry
from nse_scanner.research.mfe_mae import calculate_mfe_mae


def _price_df():
    # signal at index 2 (Close=100). Index 3 is T+1.
    return pd.DataFrame(
        {
            "Open": [10, 20, 100, 105, 110, 108, 120],
            "High": [11, 21, 101, 115, 118, 112, 125],
            "Low": [9, 19, 99, 103, 107, 100, 115],
            "Close": [10, 20, 100, 108, 112, 106, 122],
        }
    )


def test_next_open_entry_uses_t_plus_1_open_not_signal_close():
    df = _price_df()
    entry = resolve_entry(df, signal_pos=2, method=NEXT_OPEN)
    assert entry.entry_pos == 3
    assert entry.entry_price == 105  # T+1 open, NOT the signal close of 100
    assert entry.gap_from_signal_to_entry_pct == (105 - 100) / 100 * 100


def test_next_close_entry_uses_t_plus_1_close():
    df = _price_df()
    entry = resolve_entry(df, signal_pos=2, method=NEXT_CLOSE)
    assert entry.entry_price == 108  # T+1 close


def test_no_entry_when_signal_is_last_bar():
    df = _price_df()
    entry = resolve_entry(df, signal_pos=len(df) - 1, method=NEXT_OPEN)
    assert entry.entry_pos is None
    assert entry.entry_price is None


def test_forward_return_measured_from_entry_not_signal():
    df = _price_df()
    entry = resolve_entry(df, signal_pos=2, method=NEXT_OPEN)  # entry_pos=3, price=105
    # 1D forward return: entry close (108) is at entry_pos itself... horizon is measured as
    # entry_pos + horizon, so 1D means close at pos 4 (112) vs close at pos 3 (108)
    ret_1d = forward_return(df, entry.entry_pos, 1)
    assert ret_1d == (112 - 108) / 108 * 100


def test_mfe_mae_next_open_includes_entry_bar_range():
    df = _price_df()
    entry = resolve_entry(df, signal_pos=2, method=NEXT_OPEN)  # entry_pos=3, entry_price=105
    exc = calculate_mfe_mae(df, entry.entry_pos, entry.entry_price, horizon_days=3, entry_method=NEXT_OPEN)
    # Window: bars 3,4,5,6 (entry_pos..entry_pos+3). Highs: 115,118,112,125 -> max 125 (bar6)
    # Lows: 103,107,100,115 -> min 100 (bar5)
    assert exc.mfe_pct == (125 - 105) / 105 * 100
    assert exc.mae_pct == (100 - 105) / 105 * 100


def test_mfe_mae_next_close_excludes_entry_bar_range():
    df = _price_df()
    entry = resolve_entry(df, signal_pos=2, method=NEXT_CLOSE)  # entry_pos=3, entry_price=108
    exc = calculate_mfe_mae(df, entry.entry_pos, entry.entry_price, horizon_days=3, entry_method=NEXT_CLOSE)
    # Window excludes bar 3's own range (position existed only from bar3's CLOSE) -> bars 4,5,6
    # Highs: 118,112,125 -> max 125; Lows: 107,100,115 -> min 100
    assert exc.mfe_pct == (125 - 108) / 108 * 100
    assert exc.mae_pct == (100 - 108) / 108 * 100


def test_mfe_mae_next_close_differs_from_next_open_window():
    """Directly proves the entry-timing bug this module guards against: using the wrong window
    changes the result, so the two entry methods must NOT produce the same MFE/MAE here."""
    df = _price_df()
    entry_open = resolve_entry(df, signal_pos=2, method=NEXT_OPEN)
    entry_close = resolve_entry(df, signal_pos=2, method=NEXT_CLOSE)
    exc_open = calculate_mfe_mae(df, entry_open.entry_pos, entry_open.entry_price, 3, NEXT_OPEN)
    exc_close = calculate_mfe_mae(df, entry_close.entry_pos, entry_close.entry_price, 3, NEXT_CLOSE)
    assert exc_open.mfe_pct != exc_close.mfe_pct
