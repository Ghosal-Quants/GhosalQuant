"""
Static Data Updater for Polygon.io aggregates

Updates local Parquet stores from a watchlist stored in `following.parquet`.

Current scope (per user spec):
- Handle only **stocks** and **options** for now (futures & treasury left for later).
- Route by `type` column in following.parquet to the correct endpoint.
- Store updates into the *main* Parquet (schema flattened to just what we care about: ticker + bar fields).
- If a ticker already exists in the Parquet, fetch from its last timestamp+1ms to (now - 15 minutes).
  Otherwise, fetch from (now - 2 years + 1 day) to (now - 15 minutes).

Main Parquet schema (flattened):
    ticker, t, o, h, l, c, v, n, vw
Where fields mirror Polygon `results` objects:
    c=close, h=high, l=low, n=number of transactions, o=open, t=unix ms, v=volume, vw=vol-weighted avg price

Environment:
- Requires POLYGON_API_KEY in environment variables.
- Uses pandas + pyarrow for Parquet IO; `requests` for HTTP.

Usage (run from repo root or adjust paths via env):
    python -m data_layer.staticUpdater

You can override defaults with env vars:
    FOLLOWING_PARQUET   -> path to following.parquet (default: data_layer/following.parquet)
    MAIN_PARQUET        -> path to main.parquet (default: data_layer/main.parquet)
    AGG_MULTIPLIER      -> e.g. "1" (default: 1)
    AGG_TIMESPAN        -> e.g. "second" (default: second)
    START_LOOKBACK_DAYS -> integer days to look back for new tickers (default: 365*2 + 1)

Notes:
- The script will gracefully skip unknown types for now (e.g., futures, treasury) but logs them.
- Designed to be idempotent: de-duplicates on (ticker, t).
"""
from __future__ import annotations

import os
import sys
import time
import math
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
import requests

# Load environment variables from .env if present
from dotenv import load_dotenv
load_dotenv()

# ---------------------------
# Configuration
# ---------------------------
API_BASE = "https://api.polygon.io"
API_KEY = os.environ.get("POLYGON_API_KEY", "")

# File paths (can be overridden via env)
FOLLOWING_PARQUET = os.environ.get("FOLLOWING_PARQUET", os.path.join("data_layer", "following.parquet"))
MAIN_PARQUET = os.environ.get("MAIN_PARQUET", os.path.join("data_layer", "main.parquet"))

# Aggregates configuration
AGG_MULTIPLIER = int(os.environ.get("AGG_MULTIPLIER", "1"))
AGG_TIMESPAN = os.environ.get("AGG_TIMESPAN", "second")  # as per spec, we target second bars

# Lookback window for *new* tickers (days)
START_LOOKBACK_DAYS = int(os.environ.get("START_LOOKBACK_DAYS", str(365 * 2 + 1)))

# Rate limiting / retry
MAX_RETRIES = 5
BACKOFF_BASE_SEC = 1.25


# ---------------------------
# Logging
# ---------------------------
logger = logging.getLogger("staticUpdater")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


# ---------------------------
# Helpers
# ---------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _ceil_to_ms(dt: datetime) -> int:
    """Return integer milliseconds since epoch for a timezone-aware datetime."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _format_date(dt: datetime) -> str:
    """Polygon accepts ISO dates (YYYY-MM-DD) or ms-since-epoch. We'll pass ISO dates for readability."""
    return dt.strftime("%Y-%m-%d")


def _coerce_following_types(val: str) -> str:
    """Normalize the `type` column to routing keys we understand."""
    if not isinstance(val, str):
        return "unknown"
    v = val.strip().lower()
    if v in {"stock", "stocks", "equity", "equities"}:
        return "stocks"
    if v in {"option", "options"}:
        return "options"
    if v in {"future", "futures"}:
        return "futures"
    if v in {"treasury", "bond", "bonds"}:
        return "treasury"
    return "unknown"


def _aggs_url(ticker: str, route_type: str, start: datetime, end: datetime) -> str:
    # For both stocks and options we can use the same path format under /v2/aggs
    return (
        f"{API_BASE}/v2/aggs/ticker/{ticker}/range/{AGG_MULTIPLIER}/"
        f"{AGG_TIMESPAN}/{_format_date(start)}/{_format_date(end)}"
    )


def _http_get(url: str, params: Optional[Dict] = None) -> requests.Response:
    if not API_KEY:
        raise RuntimeError("POLYGON_API_KEY environment variable is required.")
    if params is None:
        params = {}
    params = {**params, "apiKey": API_KEY}

    last_err: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                # rate limited
                wait = BACKOFF_BASE_SEC * (2 ** (attempt - 1))
                logger.warning("429 Too Many Requests — backing off %.2fs (attempt %d/%d)", wait, attempt, MAX_RETRIES)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_err = e
            wait = BACKOFF_BASE_SEC * (2 ** (attempt - 1))
            logger.warning("HTTP error (%s) — retrying in %.2fs (attempt %d/%d)", getattr(e, "__class__", type(e)).__name__, wait, attempt, MAX_RETRIES)
            time.sleep(wait)
    # if we get here, give up
    assert last_err is not None
    raise last_err


def _iterate_aggs(ticker: str, route_type: str, start: datetime, end: datetime, adjusted: bool = True) -> Iterable[Dict]:
    """Yield Polygon aggregate bars for the range, handling pagination.

    The endpoint supports pagination via either `next_url` (absolute) or `next_page_token`/`next_cursor` (varies by product).
    We'll support both gracefully.
    """
    url = _aggs_url(ticker, route_type, start, end)
    params = {
        "adjusted": str(adjusted).lower(),
        "limit": 50000,  # request the max allowed batch
        # Some orgs require sort asc for time-ordered results
        "sort": "asc",
    }

    while True:
        resp = _http_get(url, params=params)
        data = resp.json()

        # Basic validation
        if data.get("status") not in {"OK", "ok", "success"} and "results" not in data:
            # Polygon sometimes returns {status:"OK"} with empty results when nothing.
            logger.info("No data or non-OK status for %s (%s): %s", ticker, route_type, data.get("status"))
            break

        results = data.get("results") or []
        for row in results:
            # Ensure ticker is present in row; fallback to provided ticker
            row.setdefault("T", None)  # some responses include uppercase T
            # Normalize to lowercase field names we expect later
            yield {
                "ticker": row.get("T") or data.get("ticker") or ticker,
                "t": row.get("t"),
                "o": row.get("o"),
                "h": row.get("h"),
                "l": row.get("l"),
                "c": row.get("c"),
                "v": row.get("v"),
                "n": row.get("n"),
                "vw": row.get("vw"),
            }

        # Detect pagination
        next_url = data.get("next_url") or data.get("nextUrl")
        next_cursor = data.get("next_cursor") or data.get("nextPageToken") or data.get("next_page_token")

        if next_url:
            # Use absolute `next_url` (no need to pass previous params)
            url = next_url
            params = {"apiKey": API_KEY}
            continue
        if next_cursor:
            # Use same endpoint but with cursor param
            params = {
                "adjusted": str(adjusted).lower(),
                "limit": 50000,
                "sort": "asc",
                "cursor": next_cursor,
            }
            # Keep using the same base URL
            continue
        break


def _load_parquet(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame(columns=["ticker", "t", "o", "h", "l", "c", "v", "n", "vw"]).astype({
            "ticker": "string",
            "t": "Int64",
            "o": "float64",
            "h": "float64",
            "l": "float64",
            "c": "float64",
            "v": "float64",
            "n": "float64",
            "vw": "float64",
        })
    return pd.read_parquet(path)


def _save_parquet(df: pd.DataFrame, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Sort and ensure dtypes
    if not df.empty:
        df = df.sort_values(["ticker", "t"])  # ascending time per ticker
    df.to_parquet(path, index=False)


def _get_existing_range(df: pd.DataFrame, ticker: str) -> Optional[int]:
    if df.empty:
        return None
    sub = df.loc[df["ticker"] == ticker, "t"]
    if sub.empty:
        return None
    return int(sub.max())


def _get_last_timestamp_from_parquet(path: str, ticker: str) -> Optional[int]:
    """Return max 't' for a ticker using a filtered read (pyarrow engine), or None if absent."""
    if not os.path.exists(path):
        return None
    try:
        # Use filter pushdown to only load this ticker's rows
        df = pd.read_parquet(path, columns=["t", "ticker"], filters=[("ticker", "=", ticker)])
        if df.empty:
            return None
        return int(df["t"].max())
    except Exception:
        # Fallback: load minimal columns and filter in-memory (last resort)
        df = pd.read_parquet(path, columns=["t", "ticker"])
        df = df[df["ticker"] == ticker]
        if df.empty:
            return None
        return int(df["t"].max())


def _append_rows_to_main(add_df: pd.DataFrame, path: str) -> None:
    """Append rows to main parquet by loading existing once, concatenating, and writing back.
    Optimized for RAM vs cumulative growth: peak equals size(existing main) + size(add_df).
    """
    if add_df is None or add_df.empty:
        return
    if os.path.exists(path):
        existing = pd.read_parquet(path)
        combined = pd.concat([existing, add_df], ignore_index=True)
        # De-dup and sort
        combined = combined.drop_duplicates(subset=["ticker", "t"], keep="last").sort_values(["ticker", "t"]) 
        _save_parquet(combined, path)
        del existing, combined
    else:
        # Ensure correct schema and write
        _save_parquet(add_df.sort_values(["ticker", "t"]), path)


def _compute_time_window(ticker: str) -> Tuple[datetime, datetime]:
    now = _now_utc()
    end = now - timedelta(minutes=15)
    last_t_ms = _get_last_timestamp_from_parquet(MAIN_PARQUET, ticker)
    if last_t_ms is not None:
        start = datetime.fromtimestamp((last_t_ms + 1) / 1000.0, tz=timezone.utc)
    else:
        start = end - timedelta(days=START_LOOKBACK_DAYS)
    return (start, end)


def _route_supported(route_type: str) -> bool:
    return route_type in {"stocks", "options"}


def update_main_from_following():
    # Load the following list
    if not os.path.exists(FOLLOWING_PARQUET):
        raise FileNotFoundError(f"following.parquet not found at: {FOLLOWING_PARQUET}")

    following = pd.read_parquet(FOLLOWING_PARQUET)
    if not {"ticker", "type"}.issubset(following.columns):
        raise ValueError("following.parquet must contain columns: 'ticker' and 'type'")

    # Normalize types
    following = following.copy()
    following["type"] = following["type"].map(_coerce_following_types)

    total_downloaded = 0
    total_skipped = 0

    for _, row in following.iterrows():
        raw_ticker = str(row["ticker"]).strip()
        route = row["type"]

        if not _route_supported(route):
            logger.info("Skipping %s (%s) — not in current scope.", raw_ticker, route)
            total_skipped += 1
            continue

        start, end = _compute_time_window(raw_ticker)
        if start >= end:
            logger.info("Up to date for %s — start >= end (%s >= %s)", raw_ticker, start, end)
            total_skipped += 1
            continue

        logger.info(
            "Fetching %s %s from %s to %s (multiplier=%s, timespan=%s)",
            route, raw_ticker, start.date(), end.date(), AGG_MULTIPLIER, AGG_TIMESPAN
        )

        rows: List[Dict] = []
        for bar in _iterate_aggs(raw_ticker, route, start, end, adjusted=True):
            if bar.get("t") is None:
                continue
            rows.append(bar)

        if not rows:
            logger.info("No new bars for %s", raw_ticker)
            total_skipped += 1
            continue

        add_df = pd.DataFrame.from_records(rows)
        add_df = add_df.dropna(subset=["t"]).astype({"t": "int64"})

        # Drop any intra-batch duplicates just in case
        before = len(add_df)
        add_df = add_df.drop_duplicates(subset=["ticker", "t"], keep="last")
        after = len(add_df)
        if before != after:
            logger.info("Deduped %d duplicate bars within batch for %s", before - after, raw_ticker)

        # Flush this ticker's rows to disk immediately
        _append_rows_to_main(add_df, MAIN_PARQUET)
        total_downloaded += len(add_df)
        logger.info("Appended %d bars for %s (flushed)", len(add_df), raw_ticker)
        del add_df

    logger.info("Done. Downloaded %d new bars. Skipped %d entries.", total_downloaded, total_skipped)


# ---------------------------
# Entry point
# ---------------------------
if __name__ == "__main__":
    try:
        update_main_from_following()
    except Exception as e:
        logger.exception("Updater failed: %s", e)
        sys.exit(1)