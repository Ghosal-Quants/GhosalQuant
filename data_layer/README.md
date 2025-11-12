

# Data Layer — Watchlist & Static Updater

This folder contains two main utilities:

- **`listEditor.py`** — a CLI to manage your `following.parquet` watchlist
- **`staticUpdater.py`** — pulls Polygon.io aggregate bars and updates local Parquet stores

Currently the updater supports **stocks** and **options** (futures/treasury scaffolding exists but is disabled in the updater by design).

---

## Quick Start

```bash
# 0) (optional) Create empty data stores
python -m data_layer.listEditor init --main

# 1) Add tickers to your following list
python -m data_layer.listEditor add stocks AAPL MSFT
python -m data_layer.listEditor add options AAPL240121C00190000

# 2) Verify
python -m data_layer.listEditor list

# 3) Provide your Polygon key (dotenv supported)
# .env file in project root:
# POLYGON_API_KEY=your_polygon_key_here

# 4) Run the updater
python -m data_layer.staticUpdater
```

---

## `following.parquet` — structure & management

`following.parquet` is your watchlist with two columns:

- `ticker` — symbol string (e.g., `AAPL`, `MSFT`, `SPY`, `AAPL240121C00190000`)
- `type` — one of `stocks`, `options`, `futures`, `treasury` *(updater currently uses only `stocks` & `options`)*

### Manage via CLI (`listEditor.py`)

```bash
# List existing entries
python -m data_layer.listEditor list

# Add one or more tickers of a given type
python -m data_layer.listEditor add stocks AAPL MSFT
python -m data_layer.listEditor add options AAPL240121C00190000

# Remove tickers
python -m data_layer.listEditor remove AAPL,MSFT

# Update type for existing tickers
python -m data_layer.listEditor set-type options SPY

# Import from CSV (must contain columns: ticker,type)
python -m data_layer.listEditor import-csv path/to/list.csv
```

**Normalization:** `type` values are normalized (e.g., `stock` → `stocks`, `option` → `options`). Duplicates are removed by `ticker` (first occurrence kept).

**Paths:** defaults can be overridden with env vars
- `FOLLOWING_PARQUET` (default `data_layer/following.parquet`)
- `MAIN_PARQUET` (default `data_layer/main.parquet`) — used by the updater and `init` command

---

## What the updater stores (schema)

The **main** Parquet is a flattened view of Polygon aggregate bars (we store only what we care about):

| column | type    | description |
|-------:|:--------|:------------|
| ticker | string  | symbol for the bar |
| t      | Int64   | unix time in **milliseconds** |
| o      | float64 | open |
| h      | float64 | high |
| l      | float64 | low |
| c      | float64 | close |
| v      | float64 | volume |
| n      | float64 | number of transactions |
| vw     | float64 | volume-weighted average price |

---

## How `staticUpdater.py` works

### Overview
- Uses Polygon **Aggregates** endpoint:
  - `/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}`
  - Defaults: `multiplier=1`, `timespan=second` *(configurable via env)*
- Supports **stocks** and **options** based on the `type` column in `following.parquet`.
- **Per-ticker flush**: after each ticker is processed, new rows are appended to `main.parquet` and written to disk immediately to reduce RAM usage.

### Time windows
- **Existing tickers**: fetch from **last saved bar + 1 ms** → **now − 15 minutes**.
- **New tickers**: fetch from **(now − 2 years + 1 day)** → **now − 15 minutes**.

The 15-minute buffer avoids near-real-time edge-cases.

### Pagination & rate limits
- Requests are **one per ticker per time window** (not one per second). Large ranges paginate (up to ~50k bars/page). The updater follows `next_url` or `cursor` until completion.
- Handles 429 rate limits with exponential backoff.

### Deduplication
- Intra-batch duplicates are dropped by (`ticker`, `t`).
- When appending, the file is read, concatenated with new rows, deduped by (`ticker`,`t`), sorted by (`ticker`,`t`), then written back.

### Environment & configuration
- **API key:** `POLYGON_API_KEY` (read from environment; `.env` is supported via `python-dotenv`).
- **Paths:**
  - `FOLLOWING_PARQUET` (default `data_layer/following.parquet`)
  - `MAIN_PARQUET` (default `data_layer/main.parquet`)
- **Aggregates config:**
  - `AGG_MULTIPLIER` (default `1`)
  - `AGG_TIMESPAN` (default `second`)
  - `START_LOOKBACK_DAYS` (default `365*2 + 1`)

Example `.env`:
```env
POLYGON_API_KEY=your_polygon_key_here
FOLLOWING_PARQUET=data_layer/following.parquet
MAIN_PARQUET=data_layer/main.parquet
AGG_MULTIPLIER=1
AGG_TIMESPAN=second
```

### Running the updater
```bash
# Ensure .env has POLYGON_API_KEY (or export it in your shell)
python -m data_layer.staticUpdater
```

You’ll see logs like:
```
Fetching stocks AAPL from 2024-11-11 to 2025-11-12 (multiplier=1, timespan=second)
Appended 123456 bars for AAPL (flushed)
...
Done. Downloaded 789012 new bars. Skipped 2 entries.
```

---

## Reading `main.parquet`

```python
import pandas as pd

main_path = "data_layer/main.parquet"
df = pd.read_parquet(main_path)

# Convert ms timestamp to readable datetime
df = df.assign(ts=pd.to_datetime(df["t"], unit="ms", utc=True))

print(df.shape)
print(df["ticker"].unique())
print(df.sort_values(["ticker","ts"]).groupby("ticker").tail(3))
```

---

## Tips & Notes
- For **large watchlists**, per-ticker flushing keeps memory bounded but increases I/O. If needed, we can move to a **partitioned Parquet dataset** (e.g., partition by `ticker`) for true append-only writes.
- The updater currently **skips** unsupported types (`futures`, `treasury`). The CLI can still prepare their empty files via `init` for future use.