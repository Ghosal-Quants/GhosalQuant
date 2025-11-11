import pandas as pd

# Load your saved Parquet file
df = pd.read_parquet("data_store/AAPL.parquet")

# Preview the first few rows
print("\n🧾 First 5 rows of AAPL data:")
print(df.head())

# Show structure info (columns, datatypes, non-null counts)
print("\n📊 DataFrame info:")
print(df.info())
