import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PipelineConfig:
    data_dir: Path
    db_path: Path
    default_universe_file: Path
    ibkr_host: str
    ibkr_port: int
    ibkr_client_id: int
    ibkr_timeout_sec: int
    lookback_days: int


def load_config() -> PipelineConfig:
    data_dir = Path(os.getenv("EOD_DATA_DIR", r"C:\Users\srini\Options_chain_data"))
    db_path = Path(os.getenv("EOD_DB_PATH", str(data_dir / "US_data_eod.db")))
    default_universe_file = Path(
        os.getenv(
            "EOD_UNIVERSE_FILE",
            str(data_dir / "US_CHARTS" / "ticker_universe.xlsx"),
        )
    )

    return PipelineConfig(
        data_dir=data_dir,
        db_path=db_path,
        default_universe_file=default_universe_file,
        ibkr_host=os.getenv("IBKR_HOST", "127.0.0.1"),
        ibkr_port=int(os.getenv("IBKR_PORT", "7497")),
        ibkr_client_id=int(os.getenv("IBKR_CLIENT_ID", "33")),
        ibkr_timeout_sec=int(os.getenv("IBKR_TIMEOUT_SEC", "12")),
        lookback_days=int(os.getenv("EOD_LOOKBACK_DAYS", "45")),
    )
