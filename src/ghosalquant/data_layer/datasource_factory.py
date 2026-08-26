from ghosalquant.data_layer.parquet_polygon_datasource import ParquetPolygonDataSource
from ghosalquant.data_layer.yfinance_multi_datasource import YFinanceMultiDataSource
from ghosalquant.data_layer.live_ibkr_datasource import LiveIBKRDataSource
from ghosalquant.util.config import Config, ConfigError, DataSrcType

def datasource_from_config(cfg: Config):
    events = tuple(cfg.data.events) if cfg.data.events else ("bars",)

    match cfg.data.source:
        case DataSrcType.YFINANCE:
            _require_bars_only(events, "yfinance")
            return YFinanceMultiDataSource(
                symbols=cfg.system.symbols,
                start=cfg.time_range.start_date,
                end=cfg.time_range.end_date,
                timeframe=cfg.system.main_timeframe,
                session=(cfg.time_range.session_start_time, cfg.time_range.session_end_time)
            )
        case DataSrcType.POLYGON:
            assert cfg.data.polygon is not None
            _require_bars_only(events, "polygon")
            return ParquetPolygonDataSource(cfg)
        case DataSrcType.LIVE_IBKR:
            if cfg.data.live_ibkr is None:
                raise ValueError("DATA.live_ibkr configuration is missing.")

            return LiveIBKRDataSource(
                symbols=cfg.system.symbols,
                host=cfg.data.live_ibkr.host,
                port=cfg.data.live_ibkr.port,
                client_id=cfg.data.live_ibkr.client_id,
                bar_resolution_sec=cfg.data.live_ibkr.bar_resolution_sec,
                events=events,
                depth=cfg.data.live_ibkr.depth,
                book_resolution_sec=cfg.data.live_ibkr.book_resolution_sec,
            )


def _require_bars_only(events: tuple[str, ...], source: str) -> None:
    extra = [kind for kind in events if kind != "bars"]
    if extra:
        raise ConfigError(f"DATA.source '{source}' only provides bars; remove {extra} from DATA.events.")
