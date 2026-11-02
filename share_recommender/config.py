"""App settings, read from environment variables or a local .env file."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str = ""
    supabase_anon_key: str = ""
    market_data_provider: str = "yahoo"
    default_exchange: str = "ASX"
    anthropic_api_key: str = ""  # optional: turns on plain-English explanations

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_anon_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()


class JobSettings(BaseSettings):
    """Settings for the scheduled notification job only (GitHub Actions secrets).

    Kept apart from ``Settings`` so the Streamlit app can never pick up the service key,
    which bypasses row-level security.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str = ""
    supabase_service_role_key: str = ""
    market_data_provider: str = "yahoo"
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""
    app_url: str = "http://localhost:8501"

    @property
    def email_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_password)
