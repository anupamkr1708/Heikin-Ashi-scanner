"""Version identifiers.

The application/system version and the strategy version are versioned independently
(PART 82 of the spec) — a system release can ship without touching the strategy definition,
and a new strategy variant does not require a system version bump.
"""

__version__ = "1.2.0"          # nse-scanner application/system version
BASELINE_STRATEGY_ID = "bb_ha_v1_base"   # immutable baseline strategy identifier
