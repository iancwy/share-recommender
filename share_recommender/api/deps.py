"""FastAPI dependencies: turn the caller's Supabase access token into a repository."""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from share_recommender.data import MarketDataProvider, get_provider
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


Repo = Annotated[PortfolioRepository, Depends(get_repository)]
MarketData = Annotated[MarketDataProvider, Depends(get_market_data)]
