#!/usr/bin/env python3
"""Build OHLCV parquet files per ticker from data_layer/main.parquet.

Usage:
    python tools/build_ohlcv.py

The script expects `data_layer/main.parquet` to contain a timestamp column in milliseconds
(e.g. named `t`, `timestamp`, `timestamp_ms`, `time`, etc.) and OHLCV columns (or their
common variants). It will create `data_layer/ohlcv/` and write one parquet per ticker with
columns: datetime, o, h, l, c, v (index=False).
"""
from pathlib import Path
import os
import sys
import pandas as pd

# Paths
REPO_ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = REPO_ROOT / "data_layer" / "main.parquet"
OUT_DIR = REPO_ROOT / "data_layer" / "ohlcv"

if not INPUT_PATH.exists():
    print(f"ERROR: input file not found: {INPUT_PATH}")
    sys.exit(1)

OUT_DIR.mkdir(parents=True, exist_ok=True)

# Helpers to find candidate columns (case-insensitive)
def find_col(df, candidates):
    lower_map = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    return None

# Read parquet
print(f"Reading {INPUT_PATH} ...")
df = pd.read_parquet(INPUT_PATH)

if df.empty:
    print("Input parquet is empty. Nothing to do.")
    sys.exit(0)

# Find timestamp column
ts_candidates = ["timestamp_ms", "timestamp", "t", "ts", "time", "datetime_ms", "datetime"]
ts_col = find_col(df, ts_candidates)
if ts_col is None:
    print(f"ERROR: no timestamp column found. Searched: {ts_candidates}")
    print(f"Columns in file: {list(df.columns)}")
    sys.exit(1)

# Find ticker/symbol column
ticker_candidates = ["ticker", "symbol", "sym", "tckr", "code"]
ticker_col = find_col(df, ticker_candidates)
if ticker_col is None:
    print(f"ERROR: no ticker/symbol column found. Searched: {ticker_candidates}")
    print(f"Columns in file: {list(df.columns)}")
    sys.exit(1)

# Find OHLCV columns
open_col = find_col(df, ["o", "open", "open_price"]) or None
high_col = find_col(df, ["h", "high", "high_price"]) or None
low_col = find_col(df, ["l", "low", "low_price"]) or None
close_col = find_col(df, ["c", "close", "close_price", "last"]) or None
vol_col = find_col(df, ["v", "volume", "vol"]) or None

required_cols = {"open": open_col, "high": high_col, "low": low_col, "close": close_col, "volume": vol_col}
missing = [k for k, v in required_cols.items() if v is None]
if missing:
    print(f"ERROR: missing OHLCV columns: {missing}. Available columns: {list(df.columns)}")
    sys.exit(1)

# Convert timestamp column to datetime (auto-detect unit: ns/us/ms/s)
series = df[ts_col]
if pd.api.types.is_datetime64_any_dtype(series):
    df["datetime"] = pd.to_datetime(series)
else:
    s_nonnull = series.dropna()
    if s_nonnull.empty:
        df["datetime"] = pd.NaT
    else:
        # If numeric dtype, determine unit from magnitude
        if pd.api.types.is_numeric_dtype(s_nonnull):
            try:
                maxv = int(s_nonnull.max())
            except Exception:
                maxv = float(s_nonnull.max())
            unit = None
            if maxv > 1e17:
                unit = "ns"
            elif maxv > 1e14:
                unit = "us"
            elif maxv > 1e11:
                unit = "ms"
            elif maxv > 1e9:
                unit = "s"
            else:
                # small values - try seconds (could be fractional) as default
                unit = "s"
            try:
                df["datetime"] = pd.to_datetime(series, unit=unit, origin="unix", errors="raise")
                print(f"Detected timestamp unit='{unit}' (max={maxv}) for column '{ts_col}'")
            except Exception:
                # fallback: try generic to_datetime (handles floats, ISO strings, etc.)
                df["datetime"] = pd.to_datetime(series, errors="coerce")
                print(f"Warning: failed to parse with unit='{unit}', used generic parsing for column '{ts_col}'")
        else:
            # Non-numeric (strings) - try pandas parser
            df["datetime"] = pd.to_datetime(series, errors="coerce")
            print(f"Parsed timestamps as datetimes/strings for column '{ts_col}'")

# Ensure datetime column exists
if "datetime" not in df.columns:
    df["datetime"] = pd.to_datetime(df[ts_col], unit="ms", origin="unix", errors="coerce")

# Build per-ticker parquet files
tickers = df[ticker_col].dropna().unique()
print(f"Found {len(tickers)} tickers")

for t in tickers:
    tdf = df[df[ticker_col] == t].copy()
    if tdf.empty:
        continue
    # Use the computed datetime
    if "datetime" not in tdf.columns:
        print(f"Skipping {t}: no datetime column after conversion")
        continue
    tdf = tdf.sort_values("datetime")

    out_df = pd.DataFrame({
        "datetime": tdf["datetime"],
        "o": tdf[open_col].astype(float),
        "h": tdf[high_col].astype(float),
        "l": tdf[low_col].astype(float),
        "c": tdf[close_col].astype(float),
        "v": tdf[vol_col].astype(float),
    })

    out_df = out_df.dropna(subset=["datetime", "o", "h", "l", "c", "v"])
    out_path = OUT_DIR / f"{t}.parquet"
    try:
        out_df.to_parquet(out_path, index=False)
        print(f"Wrote {out_path} ({len(out_df)} rows)")
    except Exception as e:
        print(f"Failed to write {out_path}: {e}")

print("Done.")
