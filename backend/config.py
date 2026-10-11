from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    google_api_key: str = ""
    hf_token: str = ""
    database_url: str = "sqlite+aiosqlite:///./rca_agent.db"

    # Gemini: telemetry + repository reasoning
    # gemini-2.5-flash is no longer available to new users.
    gemini_model: str = "gemini-3.8-flash"

    # Hugging Face: code/test generation
    # Prefer stable, broadly available Qwen checkpoints instead of a provider-specific 1M variant.
    hf_code_model: str = "Qwen/Qwen2.5-Coder-32B-Instruct"
    hf_analysis_model: str = "Qwen/Qwen2.5-Coder-32B-Instruct"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
