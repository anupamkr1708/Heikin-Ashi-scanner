"""Regression coverage for `deduplicate_mainboard` -- the deterministic-identity fix from the
mainboard-universe-semantics forensic exercise (Steps 5, 9, 12). Before this existed,
`universe/mainboard.py` deduplicated via plain `drop_duplicates(subset="NSE_Symbol")` with no
preceding sort: which of an EQ/BE pair survived depended on the security master's own,
undocumented row order. That is fixed here -- NOT by changing which symbols are included (the
EQ/BE *inclusion* predicate is untouched), only by making the choice of WHICH row represents an
already-included symbol deterministic and explainable.
"""

from __future__ import annotations

import gzip

import pandas as pd
import pytest
from nse_scanner.data.nse_reports import deduplicate_mainboard, filter_mainboard_equity, parse_security_file
from nse_scanner.testing.synthetic_market_data import generate_mainboard_semantics_fixture_cases


def _mainboard_from_fixture_cases():
    raw = generate_mainboard_semantics_fixture_cases()
    parsed = parse_security_file(gzip.compress(raw.to_csv(index=False).encode("utf-8")))
    return filter_mainboard_equity(parsed)


def test_eq_row_is_preferred_over_be_row_for_the_same_symbol():
    mainboard = _mainboard_from_fixture_cases()
    kept, dropped = deduplicate_mainboard(mainboard)

    normaleq_rows = kept[kept["NSE_Symbol"] == "NORMALEQ"]
    assert len(normaleq_rows) == 1
    assert normaleq_rows.iloc[0]["Series"] == "EQ"

    dropped_row = dropped[dropped["NSE_Symbol"] == "NORMALEQ"].iloc[0]
    assert dropped_row["Series"] == "BE"
    assert dropped_row["Kept_Series"] == "EQ"
    assert dropped_row["Dedup_Reason"] == "SERIES_PREFERENCE_EQ_OVER_BE"


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
def test_result_is_independent_of_input_row_order(seed):
    """The whole point of the fix: shuffling the input must never change which row wins."""
    mainboard = _mainboard_from_fixture_cases()
    shuffled = mainboard.sample(frac=1, random_state=seed).reset_index(drop=True)

    kept_original, _ = deduplicate_mainboard(mainboard)
    kept_shuffled, _ = deduplicate_mainboard(shuffled)

    kept_original_sorted = kept_original.sort_values("NSE_Symbol").reset_index(drop=True)
    kept_shuffled_sorted = kept_shuffled.sort_values("NSE_Symbol").reset_index(drop=True)
    pd.testing.assert_frame_equal(
        kept_original_sorted[["NSE_Symbol", "Series", "ISIN"]],
        kept_shuffled_sorted[["NSE_Symbol", "Series", "ISIN"]],
    )


def test_unranked_series_sorts_after_every_preferred_series_deterministically():
    """A symbol with an EQ row and a row in a series NOT in MAINBOARD_SERIES_DEDUP_PREFERENCE
    (constructed directly here, since filter_mainboard_equity would already exclude a genuinely
    non-EQ/BE series before dedup ever sees it -- this isolates deduplicate_mainboard's own
    ranking logic) must still deterministically prefer the ranked (EQ) row."""
    df = pd.DataFrame(
        {
            "NSE_Symbol": ["X", "X", "X"],
            "Series": ["ZZ", "EQ", "BE"],  # deliberately out of preference order in the input
            "ISIN": ["INE999A01019"] * 3,
        }
    )
    kept, dropped = deduplicate_mainboard(df)
    assert len(kept) == 1
    assert kept.iloc[0]["Series"] == "EQ"
    assert set(dropped["Series"]) == {"ZZ", "BE"}


def test_same_isin_different_symbol_is_a_known_limitation_not_silently_deduped():
    """DUPEISIN_SAMESERIES shares NORMALEQ's ISIN but has a DIFFERENT NSE_Symbol (e.g. a
    corporate-action symbol change where the security master still carries a lingering old-symbol
    row). Current identity is Symbol-keyed (matching mainboard.py's actual output column), NOT
    ISIN-keyed -- deduplicate_mainboard must NOT silently merge these, since doing so would be
    inventing an ISIN-based identity model this patch does not implement (Step 6 remains open).
    This test documents the limitation rather than hiding it."""
    mainboard = _mainboard_from_fixture_cases()
    kept, _ = deduplicate_mainboard(mainboard)

    normaleq_isin = mainboard[mainboard["NSE_Symbol"] == "NORMALEQ"]["ISIN"].iloc[0]
    same_isin_rows = kept[kept["ISIN"] == normaleq_isin]
    # BOTH NORMALEQ and DUPEISIN_SAMESERIES survive as separate constituents today -- a real,
    # open identity-model gap, not a bug this patch silently papers over.
    assert set(same_isin_rows["NSE_Symbol"]) == {"NORMALEQ", "DUPEISIN_SAMESERIES"}


def test_dedup_is_a_no_op_when_no_symbol_repeats():
    df = pd.DataFrame({"NSE_Symbol": ["A", "B", "C"], "Series": ["EQ", "EQ", "BE"], "ISIN": ["I1", "I2", "I3"]})
    kept, dropped = deduplicate_mainboard(df)
    assert len(kept) == 3
    assert len(dropped) == 0


def test_missing_nse_symbol_column_is_handled_without_raising():
    """Defensive: deduplicate_mainboard must not assume its own required column exists on
    arbitrary input -- an upstream schema change should surface elsewhere (parse_security_file
    already raises for this), not crash this function."""
    df = pd.DataFrame({"Series": ["EQ", "BE"]})
    kept, dropped = deduplicate_mainboard(df)
    assert len(kept) == 2
    assert len(dropped) == 0
