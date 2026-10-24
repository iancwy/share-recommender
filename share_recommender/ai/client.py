"""Shared Claude client, settings and error handling for the AI features."""

from collections.abc import Iterator
from contextlib import contextmanager

import anthropic

from share_recommender.config import get_settings

MODEL = "claude-opus-5-5"
# If a safety classifier declines a request, the API retries it on a fallback model itself.
FALLBACK = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}


class AIUnavailable(RuntimeError):
    """Claude couldn't be used; the message is safe to show to the person."""


def client() -> anthropic.Anthropic:
    key = get_settings().anthropic_api_key
    try:
        # With no key in .env, the SDK falls back to ANTHROPIC_API_KEY or an `ant auth login`
        # profile.
        return anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
    except anthropic.AnthropicError as exc:
        raise AIUnavailable("No Anthropic API key found. Add ANTHROPIC_API_KEY to .env to turn "
                            "on the AI features.") from exc


@contextmanager
def friendly_errors() -> Iterator[None]:
    """Turn SDK errors into AIUnavailable with a message for the person."""
    try:
        yield
    except TypeError as exc:
        # The SDK only notices missing credentials when it builds the first request.
        if "authentication method" not in str(exc):
            raise
        raise AIUnavailable("No Anthropic API key found. Add ANTHROPIC_API_KEY to .env (and "
                            "save the file), then restart the app.") from exc
    except anthropic.AuthenticationError as exc:
        raise AIUnavailable("The Anthropic API key was rejected. Check ANTHROPIC_API_KEY in "
                            ".env.") from exc
    except anthropic.RateLimitError as exc:
        raise AIUnavailable("Claude is busy (rate limited). Try again in a minute.") from exc
    except anthropic.APIStatusError as exc:
        raise AIUnavailable(f"Claude returned an error ({exc.status_code}): "
                            f"{exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise AIUnavailable("Couldn't reach Claude. Check your internet connection.") from exc
