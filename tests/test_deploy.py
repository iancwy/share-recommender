"""Running on Streamlit Community Cloud, where settings come from the app's secrets."""

from streamlit import config
from streamlit.runtime.secrets import Secrets

from share_recommender.config import Settings


def test_cloud_secrets_reach_the_settings(tmp_path, monkeypatch):
    """Cloud gives settings as root-level secrets, not a .env file. Loading st.secrets (as the
    app does first thing) puts them in the environment, where Settings reads them."""
    secrets_file = tmp_path / "secrets.toml"
    secrets_file.write_text('SUPABASE_URL = "https://example.supabase.co"\n'
                            'SUPABASE_ANON_KEY = "anon-key"\n', encoding="utf-8")
    for var in ("SUPABASE_URL", "SUPABASE_ANON_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here, as on Cloud
    original = config.get_option("secrets.files")
    config.set_option("secrets.files", [str(secrets_file)])
    secrets = Secrets()
    try:
        assert not Settings().supabase_configured  # before loading: what Ian saw
        assert secrets.load_if_toml_exists()
        settings = Settings()
        assert settings.supabase_configured
        assert settings.supabase_url == "https://example.supabase.co"
    finally:
        secrets._reset()  # removes the environment variables it added
        config.set_option("secrets.files", original)
