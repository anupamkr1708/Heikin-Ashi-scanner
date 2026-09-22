# Contributing

This is a personal research/tooling repository, but the same discipline applies whether it's one
person or a team.

## Before changing anything

1. Read `README.md`, `AUDIT.md`, and `CODE_REVIEW.md` — especially the "Known limitations".
2. Run the full check locally: `make ci` (lint + typecheck + test + build).

## Rules that must never be silently broken

- **The baseline strategy is immutable** (`strategy/bb_ha.py`). A proposed change is a new
  variant in `strategy/registry.py`, never an in-place edit. If you think the baseline itself is
  wrong, say so explicitly in a PR description — don't quietly "fix" it.
- **No silent universe fallback.** `UniverseIntegrityError` must propagate; never catch it and
  substitute a small hard-coded list.
- **No bare `except: pass`.** Every failure path is either handled explicitly (with a typed
  exception from `exceptions.py`) and logged, or re-raised.
- **No fabricated data.** Missing values stay missing (`NaN` / explicit `UNAVAILABLE` status
  strings) — never interpolated, never silently zero-filled.
- **All optional filters default to `False`** (`config.OptionalFilterConfig`). Don't flip a
  default without an explicit, documented decision.
- **Anti-look-ahead tests must keep passing.** If you touch any indicator with a rolling or
  recursive dependency (Bollinger, Heikin-Ashi, ATR, breakout state), add or extend a case in
  `tests/unit/test_lookahead.py`.

## Testing

```bash
pytest tests/ -v
ruff check src/ scripts/ tests/
mypy src/nse_scanner
```

New indicator/strategy logic needs a hand-computed reference test (see `test_atr.py`,
`test_heikin_ashi.py` for the pattern) — not just "it runs without crashing."

## Commit style

Small, single-purpose commits. If a commit fixes a numbered bug from `AUDIT.md`, reference it
(e.g. "fixes BUG 8: separate ALL_SIGNAL_DAYS from INDEPENDENT_EVENTS").
