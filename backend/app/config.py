"""
Environment-based settings for the backend.

Values are read from a `.env` file (or actual environment variables) at the
project root. See `.env.example` for the variables this expects.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # MongoDB
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db_name: str = "yazaki_news"

    # GNews API
    gnews_api_key: str

    # Pinecone (article chunk embeddings)
    pinecone_api_key: str

    # Ollama (LLM Insight stage — daily report generation). Defaults target
    # the containerized `ollama` service reachable by Compose's internal DNS;
    # docker-compose.yml also sets OLLAMA_HOST explicitly on the backend
    # service for clarity.
    ollama_host: str = "http://ollama:11434"
    ollama_model: str = "qwen2.5:7b-instruct-q4_K_M"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


# Single shared instance imported by the rest of the app,
# e.g.: from app.config import settings
settings = Settings()