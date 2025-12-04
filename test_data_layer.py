#!/usr/bin/env python3
"""Quick test for DataLayer - edit the variables below to test different settings"""
from data_layer.data_layer import DataLayer

# ========== EDIT THESE TO TEST ==========
SYMBOL = 'AAPL'        # Try: 'AAPL', 'MSFT', 'NVDA' 
TIMEFRAME = '1m'       # Try: '1s', '5s', '1m', '5m', '1h', '1d'
START_TIME = None      # Try: '2023-11-16 10:00:00' or None
END_TIME = None        # Try: '2023-11-16 12:00:00' or None
# ========================================

# Test it
dl = DataLayer()
df = dl.get(SYMBOL, start=START_TIME, end=END_TIME, timeframe=TIMEFRAME)

print(f"✅ Got {len(df)} rows of {SYMBOL} {TIMEFRAME} data")
if START_TIME or END_TIME:
    print(f"📅 Filtered from {START_TIME} to {END_TIME}")
print(f"📊 Date range: {df.index.min()} to {df.index.max()}")
print("\nFirst 3 rows:")
print(df.head(3))
