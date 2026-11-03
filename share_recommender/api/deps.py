"""FastAPI dependencies: turn the caller's Supabase access token into a repository."""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from share_recommender.ai.picker import Ranked
from share_recommender.data import MarketDataProvider, get_provider
from share_recommender.db.price_store import SupabasePriceStore
from share_recommender.db.repository import PortfolioRepository
from share_recommender.db.supabase_client import user_client

_bearer = HTTPBearer(auto_error=False)


def get_repository(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> PortfolioRepository:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in and send the Supabase access "
                            "token as 'Authorization: Bearer <token>'")
    # The token is checked by Supabase on every query; RLS scopes rows to its owner.
    return PortfolioRepository(user_client(creds.credentials))


def get_market_data() -> MarketDataProvider:
    return get_provider()


def get_price_store(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> SupabasePriceStore | None:
    return SupabasePriceStore(user_client(creds.credentials)) if creds else None


def get_ranker():
    """Claude's ranking call for the AI picker (tests swap in a stub)."""
    from share_recommender.ai.picker import rank

    return rank


Repo = Annotated[PortfolioRepository, Depends(get_repository)]
MarketData = Annotated[MarketDataProvider, Depends(get_market_data)]
PriceStore = Annotated[SupabasePriceStore | None, Depends(get_price_store)]
Ranker = Annotated[Callable[[dict], Ranked], Depends(get_ranker)]
