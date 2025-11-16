#!/usr/bin/env python3
"""Create a very small OHLCV sample set for teammates.

This script extracts the first N rows from any existing parquet files in
`data_layer/ohlcv/` and writes them to `data_layer/ohlcv/sample/`.

Usage:
    python tools/create_sample_ohlcv.py

The script is safe to commit (sample files are small). If no source files
are present it exits with a message.
"""
from pathlib import Path
import pandas as pd
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "data_layer" / "ohlcv"
DST_DIR = SRC_DIR / "sample"
N_ROWS = 200

SRC_DIR.mkdir(parents=True, exist_ok=True)
DST_DIR.mkdir(parents=True, exist_ok=True)

parquets = sorted([p for p in SRC_DIR.glob("*.parquet") if p.is_file() and p.name != "sample"])
if not parquets:
    print("No parquet files found in data_layer/ohlcv/. Run tools/build_ohlcv.py first or place parquet files there.")
    sys.exit(1)

for p in parquets:
    try:
        df = pd.read_parquet(p)
    except Exception as e:
        print(f"Skipping {p.name}: failed to read ({e})")
        continue
    if df.empty:
        print(f"Skipping {p.name}: empty")
        continue
    sample = df.head(N_ROWS)
    out = DST_DIR / p.name
    sample.to_parquet(out, index=False)
    print(f"Wrote sample {out} ({len(sample)} rows)")

print("Sample generation complete. Commit `data_layer/ohlcv/sample/` or share as needed.")
