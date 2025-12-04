from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

import pandas as pd


class DataLayer:
    """
    Single source of truth for reading OHLCV data.

    Loads parquet files from `data_layer/ohlcv/` (by default)
    and can return resampled bars for common timeframes.
    """

    # Mapping from human-friendly timeframe strings to pandas offset aliases
    TIMEFRAME_RULES: Mapping[str, str] = {
        "1s": "1S",
        "5s": "5S",
        "15s": "15S",
        "30s": "30S",
        "1m": "1T",
        "5m": "5T",
        "15m": "15T",
        "1h": "1H",
        "4h": "4H",
        "1d": "1D",
    }

    def __init__(self, data_dir: Optional[Path | str] = None) -> None:
        default_dir = Path(__file__).resolve().parent / "ohlcv"
        self.data_dir = Path(data_dir) if data_dir is not None else default_dir

    def _resolve_timeframe(self, timeframe: str) -> str:
        tf = str(timeframe).lower()
        if tf not in self.TIMEFRAME_RULES:
            supported = ", ".join(sorted(self.TIMEFRAME_RULES))
            raise ValueError(f"Unsupported timeframe '{timeframe}'. Supported: {supported}")
        return self.TIMEFRAME_RULES[tf]

    @staticmethod
    def _coerce_ts(value: Any) -> Optional[pd.Timestamp]:
        if value is None:
            return None
        return pd.to_datetime(value, utc=True)

    def _load_raw(self, symbol: str) -> pd.DataFrame:
        path = self.data_dir / f"{symbol.upper()}.parquet"
        if not path.exists():
            raise FileNotFoundError(f"OHLCV parquet not found for '{symbol}' at {path}")

        df = pd.read_parquet(path)

        rename_lower = {c: c.lower() for c in df.columns}
        df = df.rename(columns=rename_lower)

        required = ["datetime", "o", "h", "l", "c", "v"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(f"Missing required columns {missing} in {path}")

        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        df = df.set_index("datetime")
        df = df[["o", "h", "l", "c", "v"]]

        return df.sort_index()

    def get(
        self,
        symbol: str,
        start: Optional[Any] = None,
        end: Optional[Any] = None,
        timeframe: str = "1s",
    ) -> pd.DataFrame:
        
        """
        Fetch OHLCV data for a symbol between start/end at the requested timeframe.

        Parameters
        ----------
        symbol : str
            Ticker symbol (e.g., "AAPL").
        start : datetime-like, optional
            Inclusive start bound. Accepts anything pandas.to_datetime understands.
        end : datetime-like, optional
            Inclusive end bound. Accepts anything pandas.to_datetime understands.
        timeframe : str
            One of the supported timeframe keys (e.g., "1s", "1m", "1h").
        """
        df = self._load_raw(symbol)

        start_ts = self._coerce_ts(start)
        end_ts = self._coerce_ts(end)
        if start_ts is not None or end_ts is not None:
            df = df.loc[start_ts:end_ts]

        rule = self._resolve_timeframe(timeframe)

        agg = {
            "o": "first",
            "h": "max",
            "l": "min",
            "c": "last",
            "v": "sum",
        }

        resampled = df.resample(rule, label="left", closed="left").agg(agg)

        # Drop bars that are completely empty (can happen when resampling sparse data).
        resampled = resampled.dropna(how="all")

        return resampled.sort_index()
