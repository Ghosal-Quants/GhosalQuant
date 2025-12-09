# data_layer/listEditor.py
"""
CLI for managing `following.parquet` and initializing data Parquet stores.

Features:
- Add / remove / list / set-type tickers in following.parquet
- Import a CSV of tickers (columns: ticker,type)
- Initialize empty Parquet stores (default: main only; optional futures/treasury flags)

Env vars (optional):
- FOLLOWING_PARQUET (default: data_layer/following.parquet)
- MAIN_PARQUET      (default: data_layer/main.parquet)
- FUTURES_PARQUET   (default: data_layer/futures.parquet)
- TREASURY_PARQUET  (default: data_layer/treasury.parquet)

Notes:
- Current project scope focuses on stocks & options. Futures/Treasury init is supported as an option, but the updater ignores them for now.
"""
from __future__ import annotations

import argparse
import os
from typing import List

import pandas as pd

# ---------------------------
# Defaults & Paths
# ---------------------------
FOLLOWING_PARQUET = os.path.join(
    "data", "following.parquet"
) if "FOLLOWING_PARQUET" not in os.environ else os.environ["FOLLOWING_PARQUET"]
MAIN_PARQUET = os.path.join(
    "data", "main.parquet"
) if "MAIN_PARQUET" not in os.environ else os.environ["MAIN_PARQUET"]
FUTURES_PARQUET = os.path.join(
    "data", "futures.parquet"
) if "FUTURES_PARQUET" not in os.environ else os.environ["FUTURES_PARQUET"]
TREASURY_PARQUET = os.path.join(
    "data", "treasury.parquet"
) if "TREASURY_PARQUET" not in os.environ else os.environ["TREASURY_PARQUET"]

SUPPORTED_TYPES = {"stocks", "options", "futures", "treasury"}


# ---------------------------
# Helpers
# ---------------------------

def _ensure_dir(path: str) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)


def _coerce_type(val: str) -> str:
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
    return v


def _load_following(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame(
            {"ticker": pd.Series(dtype="string"), "type": pd.Series(dtype="string")}
        )
    return pd.read_parquet(path)


def _save_following(df: pd.DataFrame, path: str) -> None:
    _ensure_dir(path)
    if not df.empty:
        df = df.copy()
        df["ticker"] = df["ticker"].astype("string").str.strip()
        df["type"] = df["type"].astype("string").str.strip().map(_coerce_type)
        mask_supported = df["type"].isin(list(SUPPORTED_TYPES))
        if (~mask_supported).any():
            bad = sorted(df.loc[~mask_supported, "type"].dropna().unique().tolist())
            print(f"[warn] Unknown types found (kept as-is): {bad}")
        # We keep a single row per ticker for now
        df = df.drop_duplicates(subset=["ticker"], keep="first")
        df = df.sort_values(["type", "ticker"], kind="stable")
    df.to_parquet(path, index=False)


# ---------------------------
# Commands
# ---------------------------

def cmd_list(args: argparse.Namespace) -> None:
    df = _load_following(args.parquet)
    if df.empty:
        print("(empty)")
        return
    width = max(6, df["ticker"].astype(str).str.len().max())
    print("type      ", "ticker".ljust(width))
    print("-" * (10 + width))
    for _, r in df.iterrows():
        print(str(r["type"]).ljust(10), str(r["ticker"]).ljust(width))


def cmd_add(args: argparse.Namespace) -> None:
    df = _load_following(args.parquet)
    add_rows = []

    tickers: List[str] = []
    for t in args.tickers:
        tickers.extend([x for x in (y.strip() for y in t.split(",")) if x])

    rtype = _coerce_type(args.type)
    if rtype not in SUPPORTED_TYPES:
        raise SystemExit(
            f"Unsupported type: {args.type}. Use one of: {sorted(SUPPORTED_TYPES)}"
        )

    for t in tickers:
        add_rows.append({"ticker": t, "type": rtype})

    if not add_rows:
        print("Nothing to add.")
        return

    df_new = pd.concat([df, pd.DataFrame(add_rows)], ignore_index=True)
    _save_following(df_new, args.parquet)
    print(f"Added {len(add_rows)} rows. Total now: {len(df_new)}")


def cmd_remove(args: argparse.Namespace) -> None:
    df = _load_following(args.parquet)
    if df.empty:
        print("Nothing to remove; following is empty.")
        return

    tickers: List[str] = []
    for t in args.tickers:
        tickers.extend([x for x in (y.strip() for y in t.split(",")) if x])

    before = len(df)
    df = df[~df["ticker"].isin(tickers)]
    removed = before - len(df)
    _save_following(df, args.parquet)
    print(f"Removed {removed} rows. Total now: {len(df)}")


def cmd_set_type(args: argparse.Namespace) -> None:
    df = _load_following(args.parquet)

    if df.empty:
        print("following.parquet is empty — nothing to set.")
        return

    rtype = _coerce_type(args.type)
    if rtype not in SUPPORTED_TYPES:
        raise SystemExit(
            f"Unsupported type: {args.type}. Use one of: {sorted(SUPPORTED_TYPES)}"
        )

    mask = df["ticker"].isin(args.tickers)
    if not mask.any():
        print("No matching tickers found.")
        return

    df.loc[mask, "type"] = rtype
    _save_following(df, args.parquet)
    print(f"Updated type to '{rtype}' for {mask.sum()} tickers.")


def cmd_import_csv(args: argparse.Namespace) -> None:
    df = _load_following(args.parquet)
    src = pd.read_csv(args.csv)
    if not {"ticker", "type"}.issubset(src.columns):
        raise SystemExit("CSV must contain columns: ticker,type")

    src = src[["ticker", "type"]]
    merged = pd.concat([df, src], ignore_index=True)
    _save_following(merged, args.parquet)
    print(f"Imported {len(src)} rows. Total now: {len(merged)}")


def _empty_main_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": pd.Series(dtype="string"),
            "t": pd.Series(dtype="Int64"),
            "o": pd.Series(dtype="float64"),
            "h": pd.Series(dtype="float64"),
            "l": pd.Series(dtype="float64"),
            "c": pd.Series(dtype="float64"),
            "v": pd.Series(dtype="float64"),
            "n": pd.Series(dtype="float64"),
            "vw": pd.Series(dtype="float64"),
        }
    )


def _empty_futures_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": pd.Series(dtype="string"),
            "window_start": pd.Series(dtype="string"),
            "open": pd.Series(dtype="float64"),
            "high": pd.Series(dtype="float64"),
            "low": pd.Series(dtype="float64"),
            "close": pd.Series(dtype="float64"),
            "volume": pd.Series(dtype="float64"),
            "transactions": pd.Series(dtype="float64"),
            "dollar_volume": pd.Series(dtype="float64"),
            "settlement_price": pd.Series(dtype="float64"),
            "session_end_date": pd.Series(dtype="string"),
        }
    )


def _empty_treasury_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": pd.Series(dtype="string"),
            "date": pd.Series(dtype="string"),
            "yield": pd.Series(dtype="float64"),
        }
    )


def cmd_init(args: argparse.Namespace) -> None:
    created = []

    if args.main:
        df = _empty_main_df()
        _ensure_dir(args.main_path)
        df.to_parquet(args.main_path, index=False)
        created.append(args.main_path)

    if args.include_futures:
        df = _empty_futures_df()
        _ensure_dir(args.futures_path)
        df.to_parquet(args.futures_path, index=False)
        created.append(args.futures_path)

    if args.include_treasury:
        df = _empty_treasury_df()
        _ensure_dir(args.treasury_path)
        df.to_parquet(args.treasury_path, index=False)
        created.append(args.treasury_path)

    if not created:
        print("Nothing created (no targets selected). Use --main or flags to include others.")
    else:
        print("Created/overwritten:\n - " + "\n - ".join(created))


# ---------------------------
# Argparse wiring
# ---------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Edit following.parquet and initialize data stores"
    )
    p.add_argument(
        "--parquet",
        default=FOLLOWING_PARQUET,
        help=f"Path to following.parquet (default: {FOLLOWING_PARQUET})",
    )

    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("list", help="List current tickers and types")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("add", help="Add one or more tickers of a given type")
    sp.add_argument("type", help="Type for the tickers (stocks|options|futures|treasury)")
    sp.add_argument(
        "tickers", nargs="+", help="Ticker symbols (comma-separated or spaced)"
    )
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("remove", help="Remove one or more tickers")
    sp.add_argument(
        "tickers", nargs="+", help="Ticker symbols (comma-separated or spaced)"
    )
    sp.set_defaults(func=cmd_remove)

    sp = sub.add_parser("set-type", help="Update type for existing tickers")
    sp.add_argument("type", help="New type (stocks|options|futures|treasury)")
    sp.add_argument(
        "tickers", nargs="+", help="Ticker symbols (comma-separated or spaced)"
    )
    sp.set_defaults(func=cmd_set_type)

    sp = sub.add_parser(
        "import-csv", help="Import tickers from a CSV with columns: ticker,type"
    )
    sp.add_argument("csv", help="Path to CSV file")
    sp.set_defaults(func=cmd_import_csv)

    sp = sub.add_parser("init", help="Create empty data Parquet stores")
    sp.add_argument(
        "--main",
        action="store_true",
        help="Create/overwrite main.parquet (default path env MAIN_PARQUET)",
    )
    sp.add_argument(
        "--main-path",
        default=MAIN_PARQUET,
        help=f"Path to main.parquet (default: {MAIN_PARQUET})",
    )
    sp.add_argument(
        "--include-futures",
        action="store_true",
        help="Also create futures.parquet",
    )
    sp.add_argument(
        "--futures-path",
        default=FUTURES_PARQUET,
        help=f"Path to futures.parquet (default: {FUTURES_PARQUET})",
    )
    sp.add_argument(
        "--include-treasury",
        action="store_true",
        help="Also create treasury.parquet",
    )
    sp.add_argument(
        "--treasury-path",
        default=TREASURY_PARQUET,
        help=f"Path to treasury.parquet (default: {TREASURY_PARQUET})",
    )
    sp.set_defaults(func=cmd_init)

    return p

def addStock(ticker: str) -> None:
    """Helper function to add a stock ticker via the CLI."""
    main(['add', 'stocks', ticker])

def addOption(ticker: str) -> None:
    """Helper function to add an option ticker via the CLI."""
    main(['add', 'options', ticker])

def isFollowing(ticker: str) -> bool:
    """Helper function to check if a ticker is being followed."""
    parser = build_parser()
    args = parser.parse_args(['list'])
    df = _load_following(args.parquet)
    for _, r in df.iterrows():
        if r["ticker"] == ticker:
            return True
    return False


def main(argv: List[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
