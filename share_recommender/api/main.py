"""HTTP API over the core and repository, for a future web or mobile front end.

Run locally with:  uvicorn share_recommender.api.main:app --reload
Interactive docs:  http://127.0.0.1:8000/docs
"""

from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from postgrest.exceptions import APIError
from pydantic import BaseModel

from share_recommender.api.deps import MarketData, PriceStore, Repo
from share_recommender.core.exchanges import EXCHANGES
from share_recommender.core.importer import detect, parse_rows, read_csv
from share_recommender.core.models import (
    Category,
    CategoryIn,
    Holding,
    HoldingIn,
    Profile,
    ProfileIn,
)
from share_recommender.core.preferences import CategoryPreferences, InvestorPreferences
from share_recommender.core.recommender import Recommendation
from share_recommender.data import Quote
from share_recommender.data.history import CachedHistory
from share_recommender.portfolio_service import health_report, portfolio_history
from share_recommender.recommendations import build_recommendation, provider_fns

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


@app.put("/me", response_model=Profile)
def update_me(body: ProfileIn, repo: Repo) -> Profile:
    return repo.update_profile(body)


# Preferences interview ------------------------------------------------------

@app.get("/preferences", response_model=InvestorPreferences)
def get_preferences(repo: Repo) -> InvestorPreferences:
    prefs = repo.get_preferences()
    if prefs is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Preferences interview not done yet")
    return prefs


@app.put("/preferences", response_model=InvestorPreferences)
def save_preferences(body: InvestorPreferences, repo: Repo) -> InvestorPreferences:
    return repo.save_preferences(body)


@app.get("/categories/{category_id}/preferences", response_model=CategoryPreferences)
def get_category_preferences(category_id: UUID, repo: Repo) -> CategoryPreferences:
    prefs = repo.get_category_preferences(category_id)
    if prefs is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Goal questions not answered yet")
    return prefs


@app.put("/categories/{category_id}/preferences", response_model=CategoryPreferences)
def save_category_preferences(category_id: UUID, body: CategoryPreferences,
                              repo: Repo) -> CategoryPreferences:
    return repo.save_category_preferences(category_id, body)


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


# Recommendations ------------------------------------------------------------

@app.get("/categories/{category_id}/recommendation", response_model=Recommendation)
def get_recommendation(
    category_id: UUID, repo: Repo, market: MarketData,
    amount: Annotated[Decimal | None, Query(gt=0, description="New money to invest, in base "
                                                              "currency")] = None,
) -> Recommendation:
    category = next((c for c in repo.list_categories() if c.id == category_id), None)
    if category is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")
    inv = repo.get_preferences()
    if inv is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Complete the preferences interview first (PUT /preferences)")
    prefs = repo.get_category_preferences(category_id) or CategoryPreferences()
    profile = repo.get_profile()
    all_holdings = repo.list_holdings()
    return build_recommendation(
        category, inv, prefs, [h for h in all_holdings if h.category_id == category_id],
        profile.base_currency if profile else "AUD", *provider_fns(market), amount=amount,
        all_holdings=all_holdings,
    )


# Dashboard, health check and import ------------------------------------------

def _base(repo) -> str:
    profile = repo.get_profile()
    return profile.base_currency if profile else "AUD"


@app.get("/portfolio/history")
def get_portfolio_history(
    repo: Repo, market: MarketData, store: PriceStore,
    days: Annotated[int | None, Query(gt=0, description="Days back; omit for all")] = None,
) -> list[dict]:
    """Portfolio value and money invested over time, in base currency."""
    holdings = repo.list_holdings()
    if not holdings:
        return []
    history = CachedHistory(market, store)

    def fetch(symbols, currencies, base, start, end):
        return ({s: history.history(*s, start, end) for s in symbols},
                {c: history.fx_history(base, c, start, end) for c in currencies})

    today = date.today()
    first = min(h.buy_date for h in holdings)
    start = max(first, today - timedelta(days=days)) if days else first
    return [asdict(p) for p in portfolio_history(holdings, _base(repo), start, today, fetch)]


@app.get("/health-check")
def get_health_check(repo: Repo, market: MarketData) -> dict:
    categories = repo.list_categories()
    report = health_report(categories, repo.list_holdings(), repo.get_preferences(),
                           {c.id: repo.get_category_preferences(c.id) for c in categories},
                           _base(repo), *provider_fns(market))
    return {"findings": [asdict(f) for f in report.findings],
            "platforms": [asdict(p) | {"fees_pct": p.fees_pct} for p in report.platforms]}


class ImportPreviewIn(BaseModel):
    csv: str
    platform: str | None = None
    category_id: UUID | None = None
    default_exchange: str = "ASX"


@app.post("/holdings/import/preview")
def preview_import(body: ImportPreviewIn, repo: Repo) -> dict:
    """Parse a broker export without saving. POST each importable holding to /holdings."""
    header, rows, header_line = read_csv(body.csv)
    mapping = detect(header)
    if missing := mapping.missing():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"Couldn't find columns for: {', '.join(missing)}")
    parsed = parse_rows(rows, mapping, default_exchange=body.default_exchange.upper(),
                        platform=body.platform, category_id=body.category_id,
                        existing=repo.list_holdings(), header_line=header_line)
    return {"layout": mapping.layout, "columns": mapping.columns, "rows": [
        {"line": r.line, "side": r.side, "importable": r.importable, "duplicate": r.duplicate,
         "problem": r.problem, "holding": r.holding.model_dump(mode="json") if r.holding else None}
        for r in parsed]}


# Market data ----------------------------------------------------------------

@app.get("/quotes/{exchange}/{ticker}")
def get_quote(exchange: str, ticker: str, market: MarketData) -> Quote:
    if exchange.upper() not in EXCHANGES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown exchange '{exchange}'")
    quote = market.get_quote(ticker, exchange)
    if quote is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No price found for {ticker} on {exchange}")
    return quote
