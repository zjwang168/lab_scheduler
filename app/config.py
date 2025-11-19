from pydantic import BaseModel
from pathlib import Path


class Settings(BaseModel):
    # Global worker limit across all users / branches
    MAX_WORKERS: int = 4
    # At most this many distinct users may have RUNNING jobs at once
    MAX_ACTIVE_USERS: int = 3

    # Per-user enqueue rate limiting (jobs per interval seconds)
    USER_JOB_RATE_LIMIT: int = 20
    USER_JOB_RATE_INTERVAL_SECONDS: float = 10.0

    # Tiling params for WSI / image processing
    TILE_SIZE: int = 512
    TILE_OVERLAP: int = 64

    # Preview rendering size
    PREVIEW_MAX_DIM_PX: int = 1024

    # Where to write result files
    RESULTS_DIR: str = "results"


settings = Settings()

# Ensure results directory exists
Path(settings.RESULTS_DIR).mkdir(parents=True, exist_ok=True)