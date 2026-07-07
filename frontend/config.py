"""
Environment-based settings for the frontend.

Values are read from a `.env` file (or actual environment variables) at the
project root. See `.env.example` for the variables this expects.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Points at the FastAPI backend. Defaults to local dev; inside Docker
    # Compose this becomes http://backend:8000 (service name, not localhost —
    # same reasoning as MONGO_URI in the backend's docker-compose override).
    backend_api_url: str = "http://localhost:8000"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()