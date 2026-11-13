"""HTTP API over the core and repository, for a future web or mobile front end.

Run locally with:  uvicorn share_recommender.api.main:app --reload
Interactive docs:  http://127.0.0.1:8000/docs
"""

from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from postgrest.exceptions import APIError
from pydantic import BaseModel

from share_recommender.api.deps import MarketData, PriceStore, Ranker, Repo
from share_recommender.core.dates import sydney_today
from share_recommender.core.exchanges import EXCHANGES
from share_recommender.core.importer import detect, parse_rows, read_csv
from share_recommender.core.models import (
    CashAccount,
    CashAccountIn,
    Category,
    CategoryIn,
    Contribution,
    ContributionIn,
    Dividend,
    DividendIn,
    Holding,
    HoldingIn,
    Profile,
    ProfileIn,
    WatchlistItem,
    WatchlistItemIn,
)
from share_recommender.core.preferences import CategoryPreferences, InvestorPreferences
from share_recommender.core.projection import PROJECTION_DISCLAIMER
from share_recommender.core.recommender import Recommendation
from share_recommender.data import Quote
from share_recommender.data.candidates import candidate_pool
from share_recommender.data.history import CachedHistory
from share_recommender.picker_service import (
    DAILY_LIMIT,
    KEEP_RUNS,
    PickerNotReady,
    run_picker,
)
from share_recommender.portfolio_service import health_report, portfolio_history
from share_recommender.projection_service import MAX_YEARS, Scope, run_projection
from share_recommender.recommendations import build_recommendation, provider_fns
from share_recommender.watchlist_service import prepare_item

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
        all_holdings=all_holdings, cash_accounts=repo.list_cash_accounts(category_id),
    )


# Notifications --------------------------------------------------------------

@app.get("/notifications")
def list_notifications(repo: Repo) -> list[dict]:
    return repo.list_notifications()


@app.post("/notifications/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
def read_notification(notification_id: UUID, repo: Repo) -> None:
    repo.mark_notification_read(str(notification_id))


@app.delete("/notifications/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_notification(notification_id: UUID, repo: Repo) -> None:
    repo.delete_notification(str(notification_id))


# Dividends ------------------------------------------------------------------

@app.get("/dividends", response_model=list[Dividend])
def list_dividends(repo: Repo) -> list[Dividend]:
    return repo.list_dividends()


@app.post("/dividends", response_model=Dividend, status_code=status.HTTP_201_CREATED)
def create_dividend(body: DividendIn, repo: Repo) -> Dividend:
    return repo.create_dividend(body)


@app.delete("/dividends/{dividend_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dividend(dividend_id: UUID, repo: Repo) -> None:
    repo.delete_dividend(dividend_id)


# Watchlist ------------------------------------------------------------------

@app.get("/watchlist", response_model=list[WatchlistItem])
def list_watchlist(repo: Repo) -> list[WatchlistItem]:
    return repo.list_watchlist()


@app.post("/watchlist", response_model=WatchlistItem, status_code=status.HTTP_201_CREATED)
def add_to_watchlist(body: WatchlistItemIn, repo: Repo, market: MarketData) -> WatchlistItem:
    quotes_fn, _, fundamentals_fn = provider_fns(market)
    try:
        item = prepare_item(body, quotes_fn, fundamentals_fn)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    try:
        return repo.add_to_watchlist(item)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@app.put("/watchlist/{item_id}", response_model=WatchlistItem)
def update_watchlist_item(item_id: UUID, body: WatchlistItemIn, repo: Repo) -> WatchlistItem:
    return repo.update_watchlist_item(item_id, body)


@app.delete("/watchlist/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_from_watchlist(item_id: UUID, repo: Repo) -> None:
    repo.remove_from_watchlist(item_id)


@app.post("/watchlist/{item_id}/convert", response_model=Holding,
          status_code=status.HTTP_201_CREATED)
def convert_watchlist_item(item_id: UUID, body: HoldingIn, repo: Repo,
                           remove: bool = True) -> Holding:
    """Record a purchase of a watchlist item, and (by default) take it off the watchlist."""
    if repo.get_watchlist_item(item_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Watchlist item not found")
    holding = repo.create_holding(body)
    if remove:
        repo.remove_from_watchlist(item_id)
    return holding


# AI picker ------------------------------------------------------------------

def _picks_response(run, picks) -> dict:
    return {"run": run.model_dump(mode="json") if run else None,
            "picks": [asdict(p) | {"suggested_weight": str(p.suggested_weight)
                                   if p.suggested_weight is not None else None}
                      for p in picks]}


@app.get("/picks")
def get_picks(repo: Repo) -> dict:
    """The latest successful picker run and its picks (run is null before the first)."""
    run = repo.latest_pick_run()
    return _picks_response(run, repo.list_picks(run.id) if run else [])


@app.post("/picks/run")
def run_picks(repo: Repo, market: MarketData, rank: Ranker) -> dict:
    """Run the picker now. At most a few runs a day per person (429 after that)."""
    if repo.pick_runs_today() >= DAILY_LIMIT:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            f"The picker can run {DAILY_LIMIT} times a day. Try tomorrow.")
    quotes_fn, fx_fn, fundamentals_fn = provider_fns(market)
    categories = repo.list_categories()
    profile = repo.get_profile()
    try:
        result = run_picker(
            categories=categories,
            goal_prefs={c.id: repo.get_category_preferences(c.id) for c in categories},
            holdings=repo.list_holdings(), cash_accounts=repo.list_cash_accounts(),
            watchlist=repo.list_watchlist(), inv=repo.get_preferences(),
            base=profile.base_currency if profile else "AUD", quotes_fn=quotes_fn,
            fx_fn=fx_fn, fundamentals_fn=fundamentals_fn,
            history_fn=lambda s, start, end: market.get_many_history(list(s), start, end),
            pool_fn=lambda markets: candidate_pool(market, markets), rank_fn=rank,
            today=date.today(), trigger="manual", latest=repo.latest_pick_run())
    except PickerNotReady as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if result.reused:
        return _picks_response(result.run, repo.list_picks(result.run.id)) | {"reused": True}
    run_id = repo.save_pick_run(result.run, result.picks)
    repo.prune_pick_runs(KEEP_RUNS)
    run = result.run.model_copy(update={"id": UUID(str(run_id))})
    return _picks_response(run, result.picks) | {"reused": False}


# Regular contributions and projections --------------------------------------

@app.get("/contributions", response_model=list[Contribution])
def list_contributions(repo: Repo) -> list[Contribution]:
    return repo.list_contributions()


@app.post("/contributions", response_model=Contribution, status_code=status.HTTP_201_CREATED)
def add_contribution(body: ContributionIn, repo: Repo) -> Contribution:
    return repo.add_contribution(body)


@app.put("/contributions/{contribution_id}", response_model=Contribution)
def update_contribution(contribution_id: UUID, body: ContributionIn, repo: Repo
                        ) -> Contribution:
    return repo.update_contribution(contribution_id, body)


@app.delete("/contributions/{contribution_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_contribution(contribution_id: UUID, repo: Repo) -> None:
    repo.delete_contribution(contribution_id)


@app.get("/projections")
def get_projection(repo: Repo, market: MarketData, price_store: PriceStore,
                   scope: Scope = Scope.PORTFOLIO, category_id: UUID | None = None,
                   ticker: str | None = None, exchange: str | None = None,
                   amount: Annotated[Decimal | None, Query(ge=0)] = None,
                   years: Annotated[int, Query(ge=1, le=MAX_YEARS)] = 5,
                   basis: Literal["average", "past"] = "average", real: bool = False) -> dict:
    """Worst, base and best values month by month for the whole portfolio, a goal or one
    share or ETF, with money put in, the chance of reaching a goal's target and the
    assumptions used. An illustration, not a forecast."""
    if scope is Scope.GOAL and category_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Give a category_id")
    if scope is Scope.SHARE and not (ticker and exchange):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Give a ticker and exchange")
    quotes_fn, fx_fn, fundamentals_fn = provider_fns(market)
    history = CachedHistory(market, price_store)

    def history_fn(symbols, currencies, base, start, end):
        return ({s: history.history(*s, start, end) for s in symbols},
                {c: history.fx_history(base, c, start, end) for c in currencies})

    categories = repo.list_categories()
    profile = repo.get_profile()
    p = run_projection(
        scope=scope, category_id=category_id, ticker=ticker.upper() if ticker else None,
        exchange=exchange.upper() if exchange else None, amount=amount,
        holdings=repo.list_holdings(), cash_accounts=repo.list_cash_accounts(),
        categories=categories, contributions=repo.list_contributions(),
        goal_prefs={c.id: repo.get_category_preferences(c.id) for c in categories},
        inv=repo.get_preferences(), base=profile.base_currency if profile else "AUD",
        quotes_fn=quotes_fn, fx_fn=fx_fn, history_fn=history_fn,
        fundamentals_fn=fundamentals_fn, years=years, basis=basis, real=real,
        today=sydney_today())
    return {"points": [asdict(pt) for pt in p.points], "start_value": p.start_value,
            "contributed": p.contributed, "growth": p.growth,
            "expected_return": p.expected_return, "volatility": p.volatility,
            "chance_of_target": p.chance_of_target,
            "assets": [asdict(a) for a in p.assets], "notes": p.notes,
            "disclaimer": PROJECTION_DISCLAIMER}


# Cash accounts --------------------------------------------------------------

@app.get("/cash-accounts", response_model=list[CashAccount])
def list_cash_accounts(repo: Repo, category_id: UUID | None = None) -> list[CashAccount]:
    return repo.list_cash_accounts(category_id)


@app.post("/cash-accounts", response_model=CashAccount, status_code=status.HTTP_201_CREATED)
def create_cash_account(body: CashAccountIn, repo: Repo) -> CashAccount:
    return repo.create_cash_account(body)


@app.put("/cash-accounts/{account_id}", response_model=CashAccount)
def update_cash_account(account_id: UUID, body: CashAccountIn, repo: Repo) -> CashAccount:
    return repo.update_cash_account(account_id, body)


@app.delete("/cash-accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cash_account(account_id: UUID, repo: Repo) -> None:
    repo.delete_cash_account(account_id)


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
    accounts = repo.list_cash_accounts()
    if not holdings and not accounts:
        return []
    history = CachedHistory(market, store)

    def fetch(symbols, currencies, base, start, end):
        return ({s: history.history(*s, start, end) for s in symbols},
                {c: history.fx_history(base, c, start, end) for c in currencies})

    today = date.today()
    first = min([h.buy_date for h in holdings] + [a.as_of for a in accounts])
    start = max(first, today - timedelta(days=days)) if days else first
    return [asdict(p) for p in portfolio_history(holdings, _base(repo), start, today, fetch,
                                                 cash=accounts)]


@app.get("/health-check")
def get_health_check(repo: Repo, market: MarketData) -> dict:
    categories = repo.list_categories()
    report = health_report(categories, repo.list_holdings(), repo.get_preferences(),
                           {c.id: repo.get_category_preferences(c.id) for c in categories},
                           _base(repo), *provider_fns(market),
                           cash_accounts=repo.list_cash_accounts())
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
