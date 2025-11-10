# Execution System Design

- Structurally, the execution system is its own submodule within the overall system.
- The "data parser" section simply queries the DataSource (API) to get the data, and upon new data being available, it
calls the `on_market()` method of the Strategy.
- `on_market()` divides the entries into bars/candles, and calls the strategy for each of these.