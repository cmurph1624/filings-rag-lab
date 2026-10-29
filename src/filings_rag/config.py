from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://frl:frl@127.0.0.1:5434/frl"
    sec_user_agent: str = ""
    voyage_api_key: str = ""
    anthropic_api_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
