"""FastAPI backend for branch-aware, multi-tenant workflow scheduler.
This file just marks `app` as a package.
"""

# Nothing else needed here.


# app/config.py

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings.

    Uses pydantic-settings so it works with Pydantic v2.
    You can override these values via environment variables if needed.
    """

    # Maximum number of concurrent worker tasks (global concurrency)
    MAX_WORKERS: int = 4

    # Maximum number of distinct users that may have RUNNING jobs at once
    MAX_ACTIVE_USERS: int = 3

    # Fake output directory for generated results
    RESULT_DIR: str = "./results"


settings = Settings()
