from ghosalquant.data_layer.parquet_polygon_datasource import ParquetPolygonDataSource
from ghosalquant.data_layer.yfinance_multi_datasource import YFinanceMultiDataSource
from ghosalquant.util.config import Config, DataSrcType


def datasource_from_config(cfg: Config):
    match cfg.data.source:
        case DataSrcType.YFINANCE:
            return YFinanceMultiDataSource(
                symbols=cfg.system.symbols,
                start=cfg.time_range.start_date,
                end=cfg.time_range.end_date,
                timeframe=cfg.system.main_timeframe,
                session=(cfg.time_range.session_start_time, cfg.time_range.session_end_time)
            )
        case DataSrcType.POLYGON:
            assert cfg.data.polygon is not None
            return ParquetPolygonDataSource(cfg)