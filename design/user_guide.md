# Guide to using the GhosalQuant library (for QRs)
This guide provides a high level overview of how to get started using the GhosalQuant library for developing strategies 
and backtesting.

## Installation
To install the GhosalQuant library, you can use pip:
```bash
pip install -e .
```
from the root directory of the project. This will install the library in editable mode, allowing you to make changes to 
the code and have them reflected immediately, and keep up to date with the remote repository.

## Creating a Strategy
The way strategies are created in GhosalQuant is by creating a class which inherits from the `Strategy` base class as 
follows (hopefully your strategies will be more intelligent than this example!):
```python
from ghosalquant import Strategy
from ghosalquant.core.types import Bar, Signal, SignalType

class MyStrategy(Strategy):
    def __init__(self):
        super().__init__()
        self.prev_bar: Bar | None = None

    def on_start(self): # Required, but can be left empty
        print("MyStrategy started executing.")

    def on_end(self): # Required, but can be left empty
        print("MyStrategy finished executing.")
        # You can use this to perform any cleanup or final calculations at the end of a backtest or trading session

    def on_market_data(self, bar: Bar) -> Signal | None:
        if self.prev_bar is None:
            self.prev_bar = bar
            return None

        signal_type: SignalType

        if bar.close > self.prev_bar.close:
            signal_type = SignalType.SELL
        elif bar.close < self.prev_bar.close:
            signal_type = SignalType.BUY
        else:
            self.prev_bar = bar
            return None

        self.prev_bar = bar
        confidence = 1.0 # Actual strategies should employ more robust confidence calculations
        # A confidence of 1.0 will mean the risk engine will be unlikely to reject or downscale the signal
        # A confidence of 0.0 will mean the risk engine will be likely to reject or downscale the signal

        return Signal(
            time_stamp= bar.time_stamp,
            symbol= bar.symbol,
            signal_type= signal_type,
            price= bar.close,
            confidence= confidence,
        )
```

## Configuring a Backtest
Now that you have a strategy, you can backtest it on some historical data. The GhosalQuant library does provide some 
backtesters, but you first need to specify some things before you can run a backtest.

See `examples/config.yaml` for an example of a backtest configuration file. These are the sections which need to be 
specified in the configuration file:

### Specifying Data Sources in the Backtest Configuration File
Some common examples of data sources include:
- YFinance (`YFinanceDataSource`)
- Polygon (`ParquetPolygonDataSource`)
- pandas `DataFrame`s (`DataFrameDataSource`)
- Lists of `Bar` objects (`ListDataSource`)

