"""Regression coverage for `compute_mainboard_diagnostics` (Steps 10-11 of the
mainboard-universe-semantics forensic exercise: "make inclusion/exclusion explainable" and
"cardinality diagnostics"). This function DESCRIBES what filter_mainboard_equity/
deduplicate_mainboard already did -- it filters nothing itself, so these tests are about
accuracy of the funnel numbers and field-presence detection, not about universe membership.
"""

from __future__ import annotations

import gzip

from nse_scanner.data.nse_reports import (
    MAINBOARD_DIAGNOSTIC_FIELDS,
    compute_mainboard_diagnostics,
    deduplicate_mainboard,
    filter_mainboard_equity,
    parse_security_file,
)
from nse_scanner.testing.synthetic_market_data import (
    generate_mainboard_semantics_fixture_cases,
    generate_synthetic_security_file,
)


def _parsed_fixture_cases():
    raw = generate_mainboard_semantics_fixture_cases()
    return parse_security_file(gzip.compress(raw.to_csv(index=False).encode("utf-8")))


def test_cardinality_funnel_is_internally_consistent_on_the_case_fixture():
    """11 raw rows: EQ x8, BE x1, BZ x1 (excluded, unranked/not in {EQ,BE}), N1 x1 (excluded,
    debenture-style series) -> 9 EQ+BE rows -> 1 dropped by dedup (the NORMALEQ BE row, since its
    EQ counterpart wins) -> 8 final."""
    parsed = _parsed_fixture_cases()
    mainboard = filter_mainboard_equity(parsed)
    kept, dropped = deduplicate_mainboard(mainboard)
    diag = compute_mainboard_diagnostics(parsed, mainboard, kept, dropped)

    assert diag["raw_row_count"] == 11
    assert diag["series_breakdown"] == {"EQ": 8, "BE": 1, "BZ": 1, "N1": 1}
    assert diag["eq_be_row_count"] == 9  # BZ and N1 excluded by filter_mainboard_equity itself
    assert diag["excluded_known_non_equity_count"] == 0  # no exclusion list supplied
    assert diag["dropped_by_dedup_count"] == 1
    assert diag["dedup_reasons"] == {"SERIES_PREFERENCE_EQ_OVER_BE": 1}
    assert diag["deduplicated_row_count"] == diag["final_constituent_count"] == 8
    # funnel must sum correctly end to end
    funnel_result = diag["eq_be_row_count"] - diag["excluded_known_non_equity_count"] - diag["dropped_by_dedup_count"]
    assert funnel_result == diag["final_constituent_count"]


def test_diagnostic_field_presence_reflects_the_real_file_shape():
    """The case fixture uses real UDiFF raw column names, so every MAINBOARD_DIAGNOSTIC_FIELDS
    entry should be detected as present."""
    parsed = _parsed_fixture_cases()
    mainboard = filter_mainboard_equity(parsed)
    kept, dropped = deduplicate_mainboard(mainboard)
    diag = compute_mainboard_diagnostics(parsed, mainboard, kept, dropped)

    assert set(diag["diagnostic_field_presence"]) == set(MAINBOARD_DIAGNOSTIC_FIELDS)
    assert all(diag["diagnostic_field_presence"].values()), diag["diagnostic_field_presence"]


def test_diagnostic_field_presence_is_false_when_the_file_lacks_those_columns():
    """The OTHER synthetic generator (legacy EQUITY_L.csv-style columns, used throughout the rest
    of the test suite) genuinely does not carry DelFlg/PrtdToTrad/etc. -- presence must be
    reported as False, not silently defaulted to True or omitted. A missing column here is real
    schema information (a different NSE file revision), not a bug to paper over."""
    raw = generate_synthetic_security_file()
    parsed = parse_security_file(gzip.compress(raw.to_csv(index=False).encode("utf-8")))
    mainboard = filter_mainboard_equity(parsed)
    kept, dropped = deduplicate_mainboard(mainboard)
    diag = compute_mainboard_diagnostics(parsed, mainboard, kept, dropped)

    assert all(v is False for v in diag["diagnostic_field_presence"].values()), diag["diagnostic_field_presence"]


def test_diagnostics_never_filters_anything_itself():
    """compute_mainboard_diagnostics must be a pure describer: calling it must not change the row
    counts that were already decided by filter_mainboard_equity/deduplicate_mainboard."""
    parsed = _parsed_fixture_cases()
    mainboard = filter_mainboard_equity(parsed)
    kept, dropped = deduplicate_mainboard(mainboard)
    before = len(kept)
    compute_mainboard_diagnostics(parsed, mainboard, kept, dropped)
    assert len(kept) == before  # unchanged by the diagnostics call
