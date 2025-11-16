README — OHLCV sample data

This folder contains tiny example OHLCV parquet files for backtesting and demos.
Files: AAPL.parquet, MSFT.parquet, NVDA.parquet (200 rows each).

Quick prerequisites
- Install Python deps: pip install pandas pyarrow

Examples

1) Read a single file (pandas)
```python
import pandas as pd
df = pd.read_parquet("data_layer/ohlcv/sample/AAPL.parquet")
print(df.head())
```

2) Load all samples (one DataFrame per ticker)
```python
from pathlib import Path
import pandas as pd
p = Path("data_layer/ohlcv/sample")
dfs = {f.stem: pd.read_parquet(f) for f in p.glob("*.parquet")}
# access AAPL: dfs['AAPL']
```

3) Combine samples into a single DataFrame for backtesting
```python
import pandas as pd
from pathlib import Path
p = Path("data_layer/ohlcv/sample")
combined = pd.concat(
    [pd.read_parquet(f).assign(ticker=f.stem) for f in p.glob("*.parquet")],
    ignore_index=True,
)
combined = combined.sort_values(["ticker","datetime"])
```

4) Convert a sample to CSV (for non-Python users)
```python
import pandas as pd
p = "data_layer/ohlcv/sample/AAPL.parquet"
pd.read_parquet(p).to_csv(p.replace('.parquet', '.csv'), index=False)
```

Notes
- Columns: datetime, o, h, l, c, v
- Parquet files are small; commit only the `sample/` folder when sharing with teammates.
- If you need another format or a smaller/larger sample, run `tools/create_sample_ohlcv.py` and adjust N_ROWS in the script.
