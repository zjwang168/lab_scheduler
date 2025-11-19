from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    MAX_WORKERS: int = 4

    MAX_ACTIVE_USERS: int = 3

    class Config:
        env_file = ".env"


settings = Settings()