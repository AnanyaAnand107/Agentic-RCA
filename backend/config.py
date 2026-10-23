from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    google_api_key: str = ""
    hf_token: str = ""
    database_url: str = "sqlite+aiosqlite:///./rca_agent.db"

    # Cost-efficient Gemini model for extraction and lightweight agent tasks.
    gemini_model: str = "gemini-3.5-flash-lite"

    # Smaller Qwen checkpoints reduce inference cost while retaining instruction/code support.
    hf_code_model: str = "Qwen/Qwen2.5-Coder-7B-Instruct"
    hf_analysis_model: str = "Qwen/Qwen2.5-7B-Instruct"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
