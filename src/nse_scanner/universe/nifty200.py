"""NIFTY 200 universe provider (PART 5 / PART 6).

Migrated from the legacy notebook's Section 4a, which already implemented the hard-fail policy
correctly (no silent fallback to a small hard-coded list — see MIGRATION.md). Preserved here with
the same behavior, moved behind the UniverseProvider interface and made independently testable.

A failed official-source download raises UniverseIntegrityError. It is never caught upstream and
replaced with a partial/backup universe (PART 6) — the only escape hatch is an explicit,
user-supplied, versioned override CSV, exactly as in the legacy notebook.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd
import requests

from nse_scanner.data.base import UniverseProvider
from nse_scanner.exceptions import UniverseIntegrityError
from nse_scanner.logging_config import get_logger

logger = get_logger(__name__)

NSE_NIFTY200_URLS = (
    "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv",
    "https://archives.nseindia.com/content/indices/ind_nifty200list.csv",
    "https://www1.nseindia.com/content/indices/ind_nifty200list.csv",
)

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/csv,application/csv,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.niftyindices.com/",
}


def parse_constituent_csv(text: str, source_label: str, min_count: int, max_count: int) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text))
    df.columns = [c.strip() for c in df.columns]

    col_map = {}
    for c in df.columns:
        lc = c.lower()
        if "symbol" in lc:
            col_map[c] = "Symbol"
        elif "company" in lc:
            col_map[c] = "Company_Name"
        elif "industry" in lc or "sector" in lc:
            col_map[c] = "Sector"
    df = df.rename(columns=col_map)

    if "Symbol" not in df.columns:
        raise UniverseIntegrityError(f"[{source_label}] 'Symbol' column not found. Columns present: {list(df.columns)}")

    df["Symbol"] = df["Symbol"].astype(str).str.strip().str.upper()
    df = df[df["Symbol"].str.len() > 0].reset_index(drop=True)

    n_raw = len(df)
    n_dupes = int(df["Symbol"].duplicated().sum())
    if n_dupes > 0:
        logger.warning("[%s] %d duplicate symbols found — de-duplicating.", source_label, n_dupes)
        df = df.drop_duplicates(subset="Symbol").reset_index(drop=True)

    if not (min_count <= len(df) <= max_count):
        raise UniverseIntegrityError(
            f"[{source_label}] constituent count {len(df)} (raw {n_raw}, {n_dupes} dupes removed) "
            f"is outside the accepted NIFTY 200 range [{min_count}, {max_count}]. Refusing to "
            f"treat this as a valid NIFTY 200 universe."
        )

    if "Company_Name" not in df.columns:
        df["Company_Name"] = df["Symbol"]
    if "Sector" not in df.columns:
        df["Sector"] = pd.NA

    return df[["Symbol", "Company_Name", "Sector"]]


@dataclass
class NSENifty200UniverseProvider(UniverseProvider):
    urls: tuple[str, ...] = NSE_NIFTY200_URLS
    override_csv_path: str | None = None
    min_count: int = 150
    max_count: int = 210
    timeout: int = 15
    headers: dict = field(default_factory=lambda: dict(NSE_HEADERS))

    def get_constituents(self) -> tuple[pd.DataFrame, str, str]:
        retrieval_ts = datetime.now(timezone.utc)

        if self.override_csv_path:
            import os

            if not os.path.exists(self.override_csv_path):
                raise UniverseIntegrityError(
                    f"constituent_override_csv_path is set to '{self.override_csv_path}' but that "
                    f"file does not exist."
                )
            with open(self.override_csv_path, "r", encoding="utf-8") as f:
                text = f.read()
            df = parse_constituent_csv(text, f"USER_OVERRIDE:{self.override_csv_path}", self.min_count, self.max_count)
            return df, f"USER_OVERRIDE_CSV:{self.override_csv_path}", retrieval_ts.isoformat()

        session = requests.Session()
        session.headers.update(self.headers)
        try:
            session.get("https://www.nseindia.com", timeout=self.timeout)
        except requests.RequestException as e:
            logger.warning("NSE homepage warm-up request failed (continuing anyway): %s", e)

        failures: list[str] = []
        for url in self.urls:
            try:
                resp = session.get(url, timeout=self.timeout)
                resp.raise_for_status()
                df = parse_constituent_csv(resp.text, url, self.min_count, self.max_count)
                logger.info("Universe source OK: %s (%d constituents)", url, len(df))
                return df, url, retrieval_ts.isoformat()
            except Exception as e:  # noqa: BLE001 - one URL failing tries the next
                failures.append(f"{url} -> {type(e).__name__}: {e}")
                logger.warning("Universe source failed [%s]: %s: %s", url, type(e).__name__, e)

        failure_report = "\n".join(f"  - {f}" for f in failures)
        raise UniverseIntegrityError(
            "Could not retrieve a valid NIFTY 200 constituent list from ANY official NSE source, "
            "and no override CSV was supplied. This repository does NOT fall back to a small "
            "hard-coded list (PART 6).\n"
            f"Attempts:\n{failure_report}\n\n"
            "To proceed: (a) re-run later if this is a transient NSE outage, or (b) download the "
            "current constituent CSV from niftyindices.com and pass it as "
            "constituent_override_csv_path."
        )
