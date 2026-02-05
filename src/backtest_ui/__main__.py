from __future__ import annotations
import sys

from backtest_ui.runner import run_from_config

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python -m backtest_ui <config.yaml> <strategy.py>")
        raise SystemExit(2)
    raise SystemExit(run_from_config(sys.argv[1], sys.argv[2]))
