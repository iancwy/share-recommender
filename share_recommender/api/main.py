"""HTTP API over the core and repository, for a future web or mobile front end.

Run locally with:  uvicorn share_recommender.api.main:app --reload
Interactive docs:  http://127.0.0.1:8000/docs
"""

from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from postgrest.exceptions import APIError

from share_recommender.api.deps import MarketData, Repo
from share_recommender.core.exchanges import EXCHANGES
from share_recommender.core.models import Category, CategoryIn, Holding, HoldingIn, Profile
from share_recommender.data import Quote

app = FastAPI(title="Share Recommender API", version="0.1.0")


@app.exception_handler(APIError)
def _supabase_error(_: Request, exc: APIError) -> JSONResponse:
    # PGRST301/PGRST303 = missing, bad or expired JWT.
    if exc.code in {"PGRST301", "PGRST302", "PGRST303"}:
        return JSONResponse({"detail": "Invalid or expired token"}, status.HTTP_401_UNAUTHORIZED)
    return JSONResponse({"detail": exc.message, "code": exc.code}, status.HTTP_400_BAD_REQUEST)


@app.exception_handler(LookupError)
def _not_found(_: Request, exc: LookupError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status.HTTP_404_NOT_FOUND)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/exchanges")
def list_exchanges() -> list[dict]:
    return [{"code": e.code, "name": e.name, "currency": e.currency} for e in EXCHANGES.values()]


@app.get("/me", response_model=Profile)
def me(repo: Repo) -> Profile:
    profile = repo.get_profile()
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found")
    return profile


# Categories -----------------------------------------------------------------

@app.get("/categories", response_model=list[Category])
def list_categories(repo: Repo) -> list[Category]:
    return repo.list_categories()


@app.post("/categories", response_model=Category, status_code=status.HTTP_201_CREATED)
def create_category(body: CategoryIn, repo: Repo) -> Category:
    return repo.create_category(body)


@app.put("/categories/{category_id}", response_model=Category)
def update_category(category_id: UUID, body: CategoryIn, repo: Repo) -> Category:
    return repo.update_category(category_id, body)


@app.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(category_id: UUID, repo: Repo) -> None:
    repo.delete_category(category_id)


# Holdings -------------------------------------------------------------------

@app.get("/holdings", response_model=list[Holding])
def list_holdings(repo: Repo, category_id: UUID | None = None) -> list[Holding]:
    return repo.list_holdings(category_id)


@app.get("/holdings/{holding_id}", response_model=Holding)
def get_holding(holding_id: UUID, repo: Repo) -> Holding:
    holding = repo.get_holding(holding_id)
    if holding is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Holding not found")
    return holding


@app.post("/holdings", response_model=Holding, status_code=status.HTTP_201_CREATED)
def create_holding(body: HoldingIn, repo: Repo) -> Holding:
    return repo.create_holding(body)


@app.put("/holdings/{holding_id}", response_model=Holding)
def update_holding(holding_id: UUID, body: HoldingIn, repo: Repo) -> Holding:
    return repo.update_holding(holding_id, body)


@app.delete("/holdings/{holding_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_holding(holding_id: UUID, repo: Repo) -> None:
    repo.delete_holding(holding_id)


# Market data ----------------------------------------------------------------

@app.get("/quotes/{exchange}/{ticker}")
def get_quote(exchange: str, ticker: str, market: MarketData) -> Quote:
    if exchange.upper() not in EXCHANGES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown exchange '{exchange}'")
    quote = market.get_quote(ticker, exchange)
    if quote is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No price found for {ticker} on {exchange}")
    return quote
