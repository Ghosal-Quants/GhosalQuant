from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Type
import importlib.util

import yaml
import yfinance as yf
from matplotlib import pyplot as plt

from ghosalquant.backtester.dataframe_backtester import DataFrameBacktester
from ghosalquant.execution.strategy_base import Strategy
from ghosalquant.util.visualisation import plot_equity_curve

from . import project_root

def _safe_stem(s: str) -> str:
    # sanitiser
    chars: list[str] = []
    for ch in s:
        if ch.isalnum() or ch in ("-", "_"):
            chars.append(ch)
        else:
            chars.append("_")

    stem = "".join(chars).strip("_")
    return stem or "run"


def _load_strategy_from_file(path: Path) -> Strategy:
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import strategy file: {path}")

    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[assignment]

    # Explicit export as STRATEGY_CLASS
    if hasattr(mod, "STRATEGY_CLASS"):
        cls = getattr(mod, "STRATEGY_CLASS")
        if isinstance(cls, type) and issubclass(cls, Strategy):
            return cls()
        raise TypeError("STRATEGY_CLASS exists but is not a Strategy subclass.")

    # As a fallback, find first Strategy subclass in module
    for obj in mod.__dict__.values():
        if isinstance(obj, type) and issubclass(obj, Strategy) and obj is not Strategy:
            return obj()

    raise ValueError(
        "No strategy found. Define STRATEGY_CLASS = YourStrategy, "
        "or include a Strategy subclass in the file."
    )


def run_from_config(config_path: str | Path, strategy_path: str | Path) -> int:
    cfg_path = Path(config_path).resolve()
    strat_path = Path(strategy_path).resolve()

    if not cfg_path.exists():
        print(f"Config not found: {cfg_path}")
        return 2
    if not strat_path.exists():
        print(f"Strategy not found: {strat_path}")
        return 2

    cfg: dict[str, Any] = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    system = cfg.get("SYSTEM", {}) or {}
    tr = cfg.get("TIME_RANGE", {}) or {}

    symbols = system.get("symbols", ["AAPL"]) or ["AAPL"]
    symbol = symbols[0]  # prototype: single symbol

    start_date = tr.get("start_date", "2024-01-01")
    end_date = tr.get("end_date", "2025-12-31")

    print("UI Runner: DataFrameBacktester")
    print(f"Config:   {cfg_path}")
    print(f"Strategy: {strat_path}")
    print(f"Symbol:   {symbol}")
    print(f"Range:    {start_date} -> {end_date}")

    df = yf.download(symbol, start=start_date, end=end_date, auto_adjust=True, progress=False)

    strategy = _load_strategy_from_file(strat_path)
    backtester = DataFrameBacktester(strategy=strategy, dataframe=df, config_path=str(cfg_path))

    kpms = backtester.run()
    print("\nKEY PERFORMANCE METRICS:\n" + str(kpms))
    print("REALISED PNL:", backtester.realised_pnl)

    # Plot and save to file
    plot_equity_curve(backtester.equity_curve, show_realised_unrealised=True)

    out_dir = project_root() / "ui_outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    strategy_name = _safe_stem(strat_path.stem)
    symbol_name = _safe_stem(symbol)
    now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_path = out_dir / f"equity_curve_{now}_{symbol_name}_{strategy_name}.png"

    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()

    print(f"Saved plot: {out_path}")

    return 0
