"""Create Supabase clients from settings."""

from share_recommender.config import Settings, get_settings
from supabase import Client, ClientOptions, create_client


class SupabaseNotConfigured(RuntimeError):
    pass


def _settings(settings: Settings | None) -> Settings:
    settings = settings or get_settings()
    if not settings.supabase_configured:
        raise SupabaseNotConfigured(
            "SUPABASE_URL and SUPABASE_ANON_KEY are not set. Copy .env.example to .env "
            "and follow docs/supabase-setup.md."
        )
    return settings


def anon_client(settings: Settings | None = None) -> Client:
    """A client for signing in. Holds the session itself once a user signs in."""
    s = _settings(settings)
    return create_client(s.supabase_url, s.supabase_anon_key,
                         options=ClientOptions(persist_session=False))


def user_client(access_token: str, settings: Settings | None = None) -> Client:
    """A client that acts as the user who owns ``access_token`` (used by the API)."""
    s = _settings(settings)
    client = create_client(
        s.supabase_url,
        s.supabase_anon_key,
        options=ClientOptions(
            persist_session=False,
            auto_refresh_token=False,
            headers={"Authorization": f"Bearer {access_token}"},
        ),
    )
    client.postgrest.auth(access_token)
    return client
