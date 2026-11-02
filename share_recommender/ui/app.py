"""Streamlit UI (MVP). Run with:  streamlit run share_recommender/ui/app.py

The UI only talks to the repository, core and data packages, so it can be
replaced by a web or mobile front end on top of the FastAPI app later.
"""

import re
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

import altair as alt
import pandas as pd
import streamlit as st
from postgrest.exceptions import APIError
from pydantic import ValidationError

from share_recommender.ai import interview
from share_recommender.ai.client import AIUnavailable
from share_recommender.ai.explain import explain
from share_recommender.config import get_settings
from share_recommender.core.category_templates import CATEGORY_TEMPLATES
from share_recommender.core.exchanges import EXCHANGES
from share_recommender.core.health import SEVERITY_ORDER
from share_recommender.core.importer import (
    Broker,
    Layout,
    Mapping,
    detect,
    guess_broker,
    parse_rows,
    read_csv,
)
from share_recommender.core.income import by_financial_year, income_in_base
from share_recommender.core.models import (
    FALLBACK_PLATFORM,
    CashAccount,
    CashAccountIn,
    Category,
    CategoryIn,
    DividendIn,
    Holding,
    HoldingIn,
    ProfileIn,
    RiskLevel,
)
from share_recommender.core.performance import Dimension, breakdown, goal_progress
from share_recommender.core.portfolio import UNCATEGORISED, totals_by_currency, value_holdings
from share_recommender.core.preferences import CategoryPreferences, GoalStyle, InvestorPreferences
from share_recommender.core.questionnaire import ABOUT_YOU, GOAL, Question
from share_recommender.core.recommender import Recommendation, horizon_years, is_speculative
from share_recommender.data import Fundamentals, Quote, get_provider
from share_recommender.data.history import CachedHistory
from share_recommender.db.price_store import SupabasePriceStore
from share_recommender.db.repository import PortfolioRepository
from share_recommender.db.supabase_client import SupabaseNotConfigured, anon_client
from share_recommender.notifier import build_notifications
from share_recommender.portfolio_service import (
    health_report,
    portfolio_history,
    total,
    valued_cash,
    valued_holdings,
)
from share_recommender.recommendations import build_recommendation
from share_recommender.ui.theme import PALETTE, SEVERITY_BADGE
from supabase import Client


def _metric_rows(items: list[tuple]) -> None:
    """Metrics two to a row (2 x 2 rather than 4 across), so they're readable on a phone.

    Each item is (label, value) or (label, value, delta)."""
    for i in range(0, len(items), 2):
        for col, item in zip(st.columns(2), items[i:i + 2], strict=False):
            col.metric(*item)


def _money(amount, currency: str, cents: bool = False) -> str:
    """'15,000 AUD', or with cents for unit prices and small amounts: '9.95 AUD'."""
    return f"{amount:,.2f} {currency}" if cents else f"{amount:,.0f} {currency}"


DISCLAIMER = ("General information from a rules-based tool, not personal financial advice. "
              "Check any investment's fees and documents before you buy.")

st.set_page_config(page_title="Shares", page_icon="📈", layout="wide",
                   initial_sidebar_state="collapsed")


# Auth -------------------------------------------------------------------------

def _client() -> Client:
    if "sb" not in st.session_state:
        st.session_state.sb = anon_client()
    return st.session_state.sb


def _signed_in_user():
    # get_session() refreshes an expired access token using the refresh token.
    session = _client().auth.get_session()
    return session.user if session else None


def _login_screen() -> None:
    st.title("📈 Share Recommender")
    sign_in, sign_up = st.tabs(["Sign in", "Create account"])

    with sign_in, st.form("sign_in"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in", type="primary"):
            try:
                _client().auth.sign_in_with_password({"email": email, "password": password})
                st.rerun()
            except Exception as exc:  # supabase_auth.errors.AuthApiError and network errors
                st.error(f"Sign in failed: {exc}")

    with sign_up, st.form("sign_up"):
        st.caption("Each person gets their own login, preferences and holdings.")
        name = st.text_input("Your name")
        email = st.text_input("Email", key="su_email")
        password = st.text_input("Password (8+ characters)", type="password", key="su_pw")
        if st.form_submit_button("Create account"):
            try:
                res = _client().auth.sign_up({
                    "email": email,
                    "password": password,
                    "options": {"data": {"display_name": name or None}},
                })
                if res.session:
                    st.rerun()
                st.success("Account created. Check your email to confirm it, then sign in.")
            except Exception as exc:
                st.error(f"Could not create account: {exc}")


# Market data ------------------------------------------------------------------

@st.cache_data(ttl=15 * 60, show_spinner="Fetching prices…")
def _quotes(symbols: tuple[tuple[str, str], ...]) -> dict[tuple[str, str], Quote | None]:
    return get_provider().get_quotes(list(symbols))


@st.cache_data(ttl=60 * 60, show_spinner="Fetching exchange rates…")
def _fx_rates(base: str, currencies: tuple[str, ...]) -> dict[str, Decimal | None]:
    return get_provider().get_fx_rates(base, list(currencies))


@st.cache_data(ttl=24 * 60 * 60,
               show_spinner="Fetching company data to rank shares (the first time takes a minute)…")
def _fundamentals(symbols: tuple[tuple[str, str], ...]
                  ) -> dict[tuple[str, str], Fundamentals | None]:
    return get_provider().get_many_fundamentals(list(symbols))


# Holdings ---------------------------------------------------------------------

def _platform_options(holdings: list[Holding], default: str) -> list[str]:
    """Platforms already used, plus the person's default."""
    return sorted({h.platform for h in holdings if h.platform} | {default})


def _platform_input(container, platforms: list[str], current: str | None, key: str):
    return container.selectbox(
        "Platform", platforms, key=key, accept_new_options=True,
        index=platforms.index(current) if current in platforms else None,
        placeholder="Choose or type any platform",
        help="Type a new name to add it. Clear it for no platform.")


def _holding_form(key: str, categories: list[Category], platforms: list[str],
                  default_platform: str, existing: Holding | None = None) -> HoldingIn | None:
    """Render the add/edit form. Returns validated input when submitted, else None."""
    settings = get_settings()
    exchanges = list(EXCHANGES)
    cat_ids = [c.id for c in categories] + [None]
    cat_names = {c.id: c.name for c in categories} | {None: "Uncategorised"}
    default_exchange = existing.exchange if existing else settings.default_exchange.upper()
    current_platform = existing.platform if existing else default_platform

    with st.form(key, clear_on_submit=existing is None):
        c1, c2 = st.columns(2)
        ticker = c1.text_input("Ticker", value=existing.ticker if existing else "",
                               placeholder="e.g. VAS")
        exchange = c2.selectbox(
            "Exchange", exchanges,
            index=exchanges.index(default_exchange) if default_exchange in exchanges else 0,
            format_func=lambda code: f"{code} ({EXCHANGES[code].currency})",
        )
        c3, c4 = st.columns(2)
        units = c3.number_input("Units", min_value=0.0, step=1.0, format="%.4f",
                                value=float(existing.units) if existing else 0.0)
        buy_price = c4.number_input("Buy price per unit", min_value=0.0, step=0.01, format="%.4f",
                                    value=float(existing.buy_price) if existing else 0.0)
        c5, c6 = st.columns(2)
        fees = c5.number_input("Fees / brokerage", min_value=0.0, step=1.0, format="%.2f",
                               value=float(existing.fees) if existing else 0.0)
        buy_date = c6.date_input("Buy date", value=existing.buy_date if existing else date.today(),
                                 max_value=date.today(), format="DD/MM/YYYY")
        c7, c8 = st.columns(2)
        category_id = c7.selectbox(
            "Category", cat_ids,
            index=cat_ids.index(existing.category_id) if existing else 0,
            format_func=lambda cid: cat_names[cid],
        )
        platform = _platform_input(c8, platforms, current_platform, f"{key}_platform")
        notes = st.text_input("Notes", value=(existing.notes or "") if existing else "")
        submitted = st.form_submit_button("Save changes" if existing else "Add holding",
                                          type="primary")

    if not submitted:
        return None
    try:
        return HoldingIn(
            ticker=ticker, exchange=exchange, category_id=category_id,
            units=Decimal(str(units)), buy_price=Decimal(str(buy_price)), fees=Decimal(str(fees)),
            buy_date=buy_date, platform=platform, notes=notes or None,
        )
    except ValidationError as exc:
        for err in exc.errors():
            field = " ".join(str(p) for p in err["loc"]).replace("_", " ").capitalize()
            st.error(f"{field}: {err['msg']}")
        return None


def _holdings_page(repo: PortfolioRepository) -> None:
    categories = repo.list_categories()
    holdings = repo.list_holdings()
    profile = repo.get_profile()
    default_platform = profile.platform_for_new if profile else FALLBACK_PLATFORM
    platforms = _platform_options(holdings, default_platform)

    st.subheader("Add a holding")
    st.caption("Enter each purchase separately. Prices are in the exchange's currency.")
    new = _holding_form("add_holding", categories, platforms, default_platform)
    if new:
        repo.create_holding(new)
        st.toast(f"Added {new.units.normalize()} × {new.ticker}")
        st.rerun()

    st.divider()
    head, refresh = st.columns([4, 1])
    head.subheader(f"Your holdings ({len(holdings)})")
    if refresh.button("↻ Refresh prices"):
        _quotes.clear()
    if not holdings:
        st.info("No holdings yet. Add your first one above.")
        return

    quotes = _quotes(tuple(sorted({(h.ticker, h.exchange) for h in holdings})))
    valuations = value_holdings(holdings, categories, quotes)

    totals = totals_by_currency(valuations)
    for col, (ccy, t) in zip(st.columns(len(totals)), totals.items(), strict=True):
        gain = t["market_value"] - t["priced_cost_base"]
        col.metric(f"Market value ({ccy})", _money(t["market_value"], ccy),
                   delta=_money(gain, ccy) if t["priced_cost_base"] else None)
    _price_message([v.last_price is not None for v in valuations])

    df = pd.DataFrame([
        {
            "Ticker": v.holding.ticker,
            "Exchange": v.holding.exchange,
            "Category": v.category_name,
            "Platform": v.holding.platform or "",
            "Units": float(v.holding.units),
            "Buy price": float(v.holding.buy_price),
            "Buy date": v.holding.buy_date,
            "Fees": float(v.holding.fees),
            "Cost base": float(v.cost_base),
            "Last price": float(v.last_price) if v.last_price is not None else None,
            "Market value": float(v.market_value) if v.market_value is not None else None,
            "Gain": float(v.gain) if v.gain is not None else None,
            "Gain %": float(v.gain_pct) if v.gain_pct is not None else None,
            "Ccy": v.holding.currency,
        }
        for v in valuations
    ])
    money = st.column_config.NumberColumn(format="%.2f")
    columns = {
        "Units": st.column_config.NumberColumn(format="%.4f"),
        "Buy price": money, "Fees": money, "Cost base": money, "Last price": money,
        "Market value": money, "Gain": money,
        "Gain %": st.column_config.NumberColumn(format="%.1f%%"),
        "Buy date": st.column_config.DateColumn(format="DD/MM/YYYY"),
    }
    st.dataframe(df[["Ticker", "Units", "Market value", "Gain %"]], hide_index=True,
                 column_config=columns)
    with st.expander("All columns"):
        st.dataframe(df, hide_index=True, column_config=columns)

    st.subheader("Edit or delete a holding")
    selected = st.selectbox(
        "Holding", holdings, index=None, key="edit_holding", placeholder="Choose a holding",
        format_func=lambda h: f"{h.ticker} ({h.exchange}) · {h.units.normalize():,f} units · "
                              f"bought {h.buy_date:%d/%m/%Y}" + (f" · {h.platform}"
                                                                 if h.platform else ""))
    if selected is not None:
        updated = _holding_form(f"edit_{selected.id}", categories, platforms, default_platform,
                                existing=selected)
        if updated:
            repo.update_holding(selected.id, updated)
            st.toast(f"Updated {updated.ticker}")
            st.rerun()

        confirm = st.checkbox("Yes, I want to delete this holding", key=f"confirm_{selected.id}")
        if st.button("Delete holding", disabled=not confirm, key=f"delete_{selected.id}"):
            repo.delete_holding(selected.id)
            st.toast(f"Deleted {selected.ticker}")
            st.rerun()

    used = sorted({h.platform for h in holdings if h.platform})
    if used:
        with st.expander("Rename a platform"):
            st.caption("Moves every holding on one platform to a new name, for example when a "
                       "broker renames itself.")
            c1, c2 = st.columns(2)
            old = c1.selectbox("Platform", used, key="rename_old")
            new_name = c2.text_input("New name", key="rename_new", max_chars=50)
            count = sum(1 for h in holdings if h.platform == old)
            confirm = st.checkbox(f"Yes, rename '{old}' on {count} holding(s)",
                                  key="rename_confirm")
            if st.button("Rename platform", disabled=not (confirm and new_name.strip())):
                moved = repo.rename_platform(old, new_name)
                st.toast(f"Moved {moved} holding(s) to {new_name.strip()}")
                st.rerun()


# Cash -------------------------------------------------------------------------

def _cash_form(key: str, categories: list[Category], institutions: list[str], base: str,
               existing: CashAccount | None = None) -> CashAccountIn | None:
    cat_ids = [c.id for c in categories] + [None]
    cat_names = {c.id: c.name for c in categories} | {None: UNCATEGORISED}
    currency = existing.currency if existing else base
    with st.form(key, clear_on_submit=existing is None):
        c1, c2 = st.columns(2)
        institution = c1.selectbox(
            "Bank or platform", institutions, key=f"{key}_institution", accept_new_options=True,
            index=institutions.index(existing.institution)
            if existing and existing.institution in institutions else None,
            placeholder="Choose or type, e.g. ING", help="Type a new name to add it.")
        account_name = c2.text_input("Account name (optional)", key=f"{key}_name",
                                     value=(existing.account_name or "") if existing else "",
                                     placeholder="e.g. Savings Maximiser")
        c3, c4 = st.columns(2)
        balance = c3.number_input(f"Balance ({currency})", min_value=0.0, step=100.0,
                                  format="%.2f", key=f"{key}_balance",
                                  value=float(existing.balance) if existing else 0.0)
        rate = c4.number_input("Interest rate (% a year)", min_value=0.0, max_value=100.0,
                               step=0.05, format="%.2f", key=f"{key}_rate",
                               value=float(existing.interest_rate) if existing else 0.0,
                               help="Used to project the goal. Update it when the rate changes.")
        c5, c6 = st.columns(2)
        category_id = c5.selectbox("Goal", cat_ids, format_func=lambda cid: cat_names[cid],
                                   index=cat_ids.index(existing.category_id) if existing else 0,
                                   key=f"{key}_goal")
        as_of = c6.date_input("Balance as at", value=existing.as_of if existing else date.today(),
                              max_value=date.today(), format="DD/MM/YYYY", key=f"{key}_as_of")
        notes = st.text_input("Notes", value=(existing.notes or "") if existing else "",
                              key=f"{key}_notes")
        submitted = st.form_submit_button("Save changes" if existing else "Add cash account",
                                          type="primary")
    if not submitted:
        return None
    try:
        return CashAccountIn(institution=institution or "", account_name=account_name,
                             balance=Decimal(str(balance)), interest_rate=Decimal(str(rate)),
                             currency=currency, as_of=as_of, category_id=category_id,
                             notes=notes)
    except ValidationError as exc:
        _show_errors(exc)
        return None


def _cash_page(repo: PortfolioRepository) -> None:
    st.caption("Cash in high-interest savings accounts. It counts in your totals, fills each "
               "goal's cash slice, and grows at its own interest rate in projections. Update "
               "balances now and then: the app doesn't connect to your bank.")
    categories = repo.list_categories()
    accounts = repo.list_cash_accounts()
    profile = repo.get_profile()
    base = profile.base_currency if profile else "AUD"
    institutions = sorted({a.institution for a in accounts}
                          | {h.platform for h in repo.list_holdings() if h.platform})

    st.subheader("Add a cash account")
    new = _cash_form("add_cash", categories, institutions, base)
    if new:
        repo.create_cash_account(new)
        st.toast(f"Added {new.label}")
        st.rerun()

    st.divider()
    if not accounts:
        st.info("No cash accounts yet. Add one above.")
        return
    st.subheader(f"Your cash ({len(accounts)})")
    names = {c.id: c.name for c in categories}
    by_ccy: dict[str, Decimal] = {}
    for a in accounts:
        by_ccy[a.currency] = by_ccy.get(a.currency, Decimal(0)) + a.balance
    for col, (ccy, amount) in zip(st.columns(len(by_ccy)), by_ccy.items(), strict=True):
        col.metric(f"Total cash ({ccy})", _money(amount, ccy))
    df = pd.DataFrame([
        {"Bank or platform": a.institution, "Account": a.account_name or "",
         "Goal": names.get(a.category_id, UNCATEGORISED) if a.category_id else UNCATEGORISED,
         "Balance": float(a.balance), "Ccy": a.currency, "Rate (% a year)": float(a.interest_rate),
         "As at": a.as_of, "Notes": a.notes or ""}
        for a in accounts
    ])
    columns = {"Balance": st.column_config.NumberColumn(format="%.2f"),
               "Rate (% a year)": st.column_config.NumberColumn(format="%.2f"),
               "As at": st.column_config.DateColumn(format="DD/MM/YYYY")}
    st.dataframe(df[["Bank or platform", "Balance", "Rate (% a year)"]], hide_index=True,
                 column_config=columns)
    with st.expander("All columns"):
        st.dataframe(df, hide_index=True, column_config=columns)

    st.subheader("Edit or delete a cash account")
    selected = st.selectbox("Cash account", accounts, index=None, key="edit_cash",
                            placeholder="Choose an account",
                            format_func=lambda a: f"{a.label} · {a.balance:,.2f} {a.currency}")
    if selected is None:
        return
    updated = _cash_form(f"edit_cash_{selected.id}", categories, institutions, base,
                         existing=selected)
    if updated:
        repo.update_cash_account(selected.id, updated)
        st.toast(f"Updated {updated.label}")
        st.rerun()
    confirm = st.checkbox("Yes, I want to delete this cash account",
                          key=f"confirm_cash_{selected.id}")
    if st.button("Delete cash account", disabled=not confirm, key=f"delete_cash_{selected.id}"):
        repo.delete_cash_account(selected.id)
        st.toast(f"Deleted {selected.label}")
        st.rerun()


# Categories -------------------------------------------------------------------

def _category_form(key: str, existing: CategoryIn | None = None) -> CategoryIn | None:
    risks = list(RiskLevel)
    with st.form(key, clear_on_submit=existing is None):
        c1, c2 = st.columns([2, 1])
        name = c1.text_input("Name", value=existing.name if existing else "")
        risk = c2.selectbox("Risk level", risks, format_func=lambda r: r.value.capitalize(),
                            index=risks.index(existing.risk_level) if existing else 1)
        description = st.text_input("Description",
                                    value=(existing.description or "") if existing else "")
        c3, c4 = st.columns(2)
        target_amount = c3.number_input(
            "Target amount (optional)", min_value=0.0, step=1000.0, format="%.2f",
            value=float(existing.target_amount) if existing and existing.target_amount else None,
        )
        target_date = c4.date_input("Target date (optional)",
                                    value=existing.target_date if existing else None,
                                    format="DD/MM/YYYY")
        submitted = st.form_submit_button("Save" if existing else "Add category", type="primary")
    if not submitted:
        return None
    try:
        return CategoryIn(
            name=name, description=description or None, risk_level=risk,
            target_amount=Decimal(str(target_amount)) if target_amount else None,
            target_date=target_date,
        )
    except ValidationError as exc:
        for err in exc.errors():
            st.error(f"{' '.join(str(p) for p in err['loc']).capitalize()}: {err['msg']}")
        return None


def _categories_page(repo: PortfolioRepository) -> None:
    categories = repo.list_categories()
    st.caption("Group holdings by goal. Each category can have its own target and risk level, "
               "which later drive recommendations and health checks.")

    for cat in categories:
        target = []
        if cat.target_amount:
            target.append(f"target {cat.target_amount:,.0f}")
        if cat.target_date:
            target.append(f"by {cat.target_date:%b %Y}")
        label = f"**{cat.name}** · {cat.risk_level.value} risk" + (
            f" · {' '.join(target)}" if target else "")
        with st.expander(label):
            updated = _category_form(f"cat_{cat.id}", existing=cat)
            if updated:
                repo.update_category(cat.id, updated)
                st.rerun()
            if st.button("Delete category", key=f"del_cat_{cat.id}",
                         help="Holdings in this category are kept and become uncategorised."):
                repo.delete_category(cat.id)
                st.rerun()

    st.divider()
    st.subheader("Add a category")
    existing_names = {c.name for c in categories}
    templates = [t for t in CATEGORY_TEMPLATES if t["name"] not in existing_names]
    if templates:
        for i in range(0, len(templates), 2):
            for col, t in zip(st.columns(2), templates[i:i + 2], strict=False):
                if col.button(f"＋ {t['name']}", key=f"tpl_{t['name']}",
                              help=t["description"], use_container_width=True):
                    repo.create_category(CategoryIn(**t))
                    st.rerun()
    st.caption("Or create your own:")
    new = _category_form("add_category")
    if new:
        if new.name in existing_names:
            st.error(f"You already have a category called '{new.name}'.")
        else:
            repo.create_category(new)
            st.rerun()


# Preferences interview --------------------------------------------------------
# The questions come from core.questionnaire, which the chat interview shares.

def _ask(q: Question, current, key: str, number: int | None = None):
    """Render one questionnaire question and return the answer."""
    label = f"{number}. {q.text}" if number else q.text
    if q.kind == "choice":
        options = list(q.options)
        return st.radio(label, options, format_func=q.options.get, key=key, help=q.help,
                        index=options.index(current) if current in options else 0)
    if q.kind == "multi":
        return st.multiselect(label, list(q.options), format_func=q.options.get, key=key,
                              default=[v for v in current if v in q.options], help=q.help)
    if q.kind == "number":
        if isinstance(current, int):
            return st.number_input(label, min_value=int(q.min), step=int(q.step), value=current,
                                   max_value=int(q.max) if q.max is not None else None,
                                   key=key, help=q.help)
        return st.number_input(label, min_value=float(q.min), step=float(q.step),
                               value=float(current), format="%.2f", key=key, help=q.help)
    if q.kind == "tickers":
        text = st.text_input(label, value=", ".join(current), key=key, help=q.help)
        return [t for t in text.replace(" ", ",").split(",") if t]
    return st.text_area(label, value=current or "", key=key, help=q.help, height=80)


def _show_errors(exc: ValidationError) -> None:
    for err in exc.errors():
        field = " ".join(str(p) for p in err["loc"]).replace("_", " ").capitalize()
        st.error(f"{field}: {err['msg']}")


def _about_you_form(inv: InvestorPreferences) -> InvestorPreferences | None:
    with st.form("interview"):
        answers = {q.field: _ask(q, getattr(inv, q.field), f"q_{q.field}", n)
                   for n, q in enumerate(ABOUT_YOU, 1)}
        submitted = st.form_submit_button("Save my answers", type="primary")
    if not submitted:
        return None
    try:
        return InvestorPreferences(**answers)
    except ValidationError as exc:
        _show_errors(exc)
        return None


def _goal_form(cat: Category, prefs: CategoryPreferences) -> CategoryPreferences | None:
    with st.form(f"goal_{cat.id}"):
        answers = {}
        for q in GOAL:
            current = getattr(prefs, q.field)
            if q.field == "horizon_years":
                if cat.target_date:
                    years = horizon_years(cat, prefs, date.today())
                    st.markdown(f"**When do you need this money?** Your target date is "
                                f"{cat.target_date:%d/%m/%Y}, about {years:g} years away. "
                                "Change it on the Categories page.")
                    answers[q.field] = current
                    continue
                current = 10 if current is None else current
            if q.field == "style":
                current = GoalStyle.SPECULATIVE if is_speculative(cat, prefs) else GoalStyle.CORE
            answers[q.field] = _ask(q, current, f"q_{q.field}_{cat.id}")
        submitted = st.form_submit_button("Save")
    if not submitted:
        return None
    if answers["style"] is GoalStyle.SPECULATIVE and not answers["themes"]:
        st.error("Choose at least one theme for a speculative goal.")
        return None
    answers["themes"] = answers["themes"] or prefs.themes
    try:
        return CategoryPreferences(**answers)
    except ValidationError as exc:
        _show_errors(exc)
        return None


def _answer_text(q: Question, value) -> str:
    if value is None or value == [] or value == "":
        return "–"
    if q.kind == "choice":
        return q.options.get(value, str(value))
    if q.kind == "multi":
        return ", ".join(q.options.get(v, str(v)) for v in value)
    if q.kind == "tickers":
        return ", ".join(value)
    return f"{value:g}" if isinstance(value, Decimal) else str(value)


def _review_rows(questions: list[Question], new, old, answered: set[str]) -> pd.DataFrame:
    rows = []
    for q in questions:
        value = getattr(new, q.field)
        if q.field in answered or old is not None:
            answer = _answer_text(q, value)
        else:
            answer = f"Not discussed yet (default: {_answer_text(q, value)})"
        changed = q.field in answered and (old is None or getattr(old, q.field) != value)
        rows.append({"": "●" if changed else "", "Question": q.text, "Answer": answer})
    return pd.DataFrame(rows)


def _draft_review(repo: PortfolioRepository, categories: list[Category], draft: interview.Draft,
                  saved: InvestorPreferences | None,
                  saved_goals: dict[UUID, CategoryPreferences]) -> None:
    ss = st.session_state
    st.subheader("Answers from the chat" + (": ready to review" if draft.complete else
                                            " so far"))
    st.caption("Nothing is saved until you click Save. ● marks an answer that is new or "
               "different from what you've saved.")
    columns = {"": st.column_config.TextColumn(width="small"),
               "Answer": st.column_config.TextColumn(width="large")}
    st.dataframe(_review_rows(ABOUT_YOU, draft.about_you, saved, draft.answered),
                 hide_index=True, column_config=columns)
    for cat in categories:
        if cat.id not in draft.goals:
            continue
        prefs = draft.goals[cat.id]
        questions = [q for q in GOAL
                     if not (q.field == "horizon_years" and cat.target_date)
                     and not (q.field == "themes" and not is_speculative(cat, prefs))]
        st.markdown(f"**{cat.name}**")
        st.dataframe(_review_rows(questions, prefs, saved_goals.get(cat.id),
                                  draft.goal_answered.get(cat.id, set())),
                     hide_index=True, column_config=columns)
    for problem in ss.get("chat_problems", []):
        st.warning(f"Couldn't use one answer, so it was left as before. {problem}")

    c1, c2 = st.columns(2)
    if c1.button("Save these answers", type="primary", key="chat_save"):
        repo.save_preferences(draft.about_you)
        for cid, prefs in draft.goals.items():
            repo.save_category_preferences(cid, prefs)
        _reset_chat()
        st.toast("Answers saved")
        st.rerun()
    if c2.button("Start the chat again", key="chat_reset"):
        _reset_chat()
        st.rerun()


def _reset_chat() -> None:
    for key in ("chat_history", "chat_draft", "chat_problems"):
        st.session_state.pop(key, None)


def _chat_tab(repo: PortfolioRepository, categories: list[Category],
              saved: InvestorPreferences | None,
              saved_goals: dict[UUID, CategoryPreferences]) -> None:
    ss = st.session_state
    st.caption("Chat with Claude (Anthropic's AI) instead of filling in the form. It fills in "
               "the same questionnaire, keeps anything extra you mention as notes, and you "
               "check the answers before they're saved.")
    history = ss.setdefault("chat_history", [])
    with st.container(height=420):
        st.chat_message("assistant").write(interview.GREETING)
        for m in history:
            st.chat_message(m["role"]).write(m["content"])
    if message := st.chat_input("Reply to Claude…", key="chat_input"):
        try:
            with st.spinner("Claude is thinking…"):
                result = interview.run_turn(history, message, categories, saved, saved_goals,
                                            ss.get("chat_draft"))
        except AIUnavailable as exc:
            st.error(str(exc))
        else:
            history += [{"role": "user", "content": message},
                        {"role": "assistant", "content": result.reply}]
            ss.chat_draft, ss.chat_problems = result.draft, result.problems
            st.rerun()
    if draft := ss.get("chat_draft"):
        _draft_review(repo, categories, draft, saved, saved_goals)


def _preferences_page(repo: PortfolioRepository) -> None:
    st.caption("A short questionnaire about you and each of your goals. Your answers drive the "
               "recommendations, and you can change them any time.")
    saved = repo.get_preferences()
    categories = repo.list_categories()
    saved_goals = {c.id: p for c in categories if (p := repo.get_category_preferences(c.id))}
    form_tab, chat_tab = st.tabs(["📝 Questionnaire", "💬 Chat with Claude"])

    with form_tab:
        st.subheader("About you" + (" ✓" if saved else ""))
        new = _about_you_form(saved or InvestorPreferences())
        if new:
            repo.save_preferences(new)
            st.toast("Answers saved")
            st.rerun()

        st.subheader("Your goals")
        st.caption("A few questions for each category. Its risk level and target are set on "
                   "the Categories page.")
        for cat in categories:
            prefs = saved_goals.get(cat.id)
            with st.expander(f"**{cat.name}** · {cat.risk_level.value} risk"
                             + (" ✓" if prefs else " · not answered yet"),
                             expanded=prefs is None):
                updated = _goal_form(cat, prefs or CategoryPreferences())
                if updated:
                    repo.save_category_preferences(cat.id, updated)
                    st.toast(f"Saved answers for {cat.name}")
                    st.rerun()

    with chat_tab:
        _chat_tab(repo, categories, saved, saved_goals)


# Recommendations --------------------------------------------------------------

@st.cache_data(ttl=24 * 60 * 60, show_spinner="Asking Claude to explain…")
def _explanation(rec_json: str, inv_json: str, prefs_json: str, category_json: str) -> str:
    return explain(Recommendation.model_validate_json(rec_json),
                   InvestorPreferences.model_validate_json(inv_json),
                   CategoryPreferences.model_validate_json(prefs_json),
                   Category.model_validate_json(category_json))


def _recommendations_page(repo: PortfolioRepository) -> None:
    inv = repo.get_preferences()
    if inv is None:
        st.info("Answer a few questions first so the recommendations fit you.")
        if st.button("Go to Preferences", type="primary"):
            st.switch_page(NAV["Preferences"])
        return
    categories = repo.list_categories()
    if not categories:
        st.info("Add a goal category first, on the Categories page.")
        return

    c1, c2 = st.columns([2, 1])
    cat = c1.selectbox("Goal", categories, format_func=lambda c: c.name, key="rec_category")
    profile = repo.get_profile()
    base = profile.base_currency if profile else "AUD"
    amount = c2.number_input(f"New money to invest ({base})", min_value=0.0, step=500.0,
                             format="%.2f", key="rec_amount",
                             help="Optional. Enter an amount to see what to buy.")
    prefs = repo.get_category_preferences(cat.id)
    if prefs is None:
        st.warning(f"You haven't answered the questions for {cat.name} yet, so a "
                   f"{'target-date' if cat.target_date else '10-year'} horizon and average "
                   "flexibility are assumed. Answer them on the Preferences page.")
        prefs = CategoryPreferences()

    all_holdings = repo.list_holdings()
    with st.spinner("Working out recommendations…"):
        rec = build_recommendation(cat, inv, prefs,
                                   [h for h in all_holdings if h.category_id == cat.id], base,
                                   _quotes, _fx_rates, _fundamentals,
                                   amount=Decimal(str(amount)) if amount else None,
                                   all_holdings=all_holdings,
                                   cash_accounts=repo.list_cash_accounts(cat.id))
    mix, gaps = rec.mix, rec.gaps

    first = ([("Goal type", "Speculative"), ("Investments", len(mix.picks))] if mix.speculative
             else [("Shares (growth)", f"{mix.growth_pct}%"),
                   ("Cash (defensive)", f"{100 - mix.growth_pct}%")])
    _metric_rows(first + [("Time horizon", f"{mix.horizon_years:g} years"),
                          ("Held in this goal", _money(gaps.total_value, base))])
    for w in mix.warnings:
        st.warning(w)
    notes = [n for n in (inv.notes, prefs.notes) if n]
    if notes:
        st.info("**Your notes:** " + " ".join(notes) + "\n\nThe rules can't read notes, so "
                "check the picks against them. The plain-English explanation below does this "
                "for you.")

    st.subheader("Target mix and suggested investments")
    pct = st.column_config.NumberColumn(format="%.1f%%")
    st.dataframe(pd.DataFrame([
        {"Slice": next(s.label for s in mix.slices if s.asset_class is p.asset_class),
         "Ticker": p.ticker, "Exchange": p.exchange, "Name": p.name,
         "Type": "ETF" if p.kind == "etf" else "Share", "Target": float(p.weight),
         "Why": p.reason}
        for p in mix.picks
    ]), hide_index=True, column_config={"Target": pct, "Why": st.column_config.TextColumn(
        width="large")})

    st.subheader("Compared with what you hold")
    if not gaps.total_value:
        st.info("Nothing is held in this goal yet. Enter an amount above to see a first "
                "purchase, or tag existing holdings with this category on the Holdings page.")
    else:
        money = st.column_config.NumberColumn(format="%.2f")
        st.dataframe(pd.DataFrame([
            {"Slice": g.label, "Target": float(g.target_pct), "Now": float(g.current_pct),
             f"Value now ({base})": float(g.current_value),
             f"Target value ({base})": float(g.target_value),
             "To reach target": float(g.difference)}
            for g in gaps.slices
        ]), hide_index=True, column_config={"Target": pct, "Now": pct,
                                            f"Value now ({base})": money,
                                            f"Target value ({base})": money,
                                            "To reach target": money})
        st.caption("A positive 'To reach target' means that slice is underweight. "
                   "Recommendations add new money rather than selling.")
    for note in gaps.notes:
        st.caption(note)

    plan = rec.buy_plan
    if plan:
        st.subheader(f"What to buy with {_money(plan.amount, base)}")
        if plan.orders:
            st.dataframe(pd.DataFrame([
                {"Ticker": o.ticker, "Exchange": o.exchange, "Name": o.name, "Units": o.units,
                 "Price": float(o.price), "Ccy": o.currency, f"Brokerage ({base})":
                 float(o.brokerage), f"Total ({base})": float(o.total_cost)}
                for o in plan.orders
            ]), hide_index=True, column_config={"Price": st.column_config.NumberColumn(
                format="%.2f")})
            st.caption(f"Left over: {_money(plan.leftover, base, cents=True)}. Prices are the "
                       "latest available and will move before you trade.")
        for t in plan.cash_topups:
            account = f"{t.institution} {t.account_name}" if t.account_name else t.institution
            st.markdown(f"**Add {_money(t.amount, base)} to {account}** "
                        f"({t.interest_rate:g}% a year)")
        for note in plan.notes:
            st.caption(note)

    st.subheader("Why this mix")
    st.markdown("\n".join(f"- {r}" for r in mix.reasons))
    if st.button("Explain it in plain English", help="Uses Claude (Anthropic's AI) to write "
                 "a short explanation from the reasons above."):
        try:
            st.session_state[f"explained_{cat.id}"] = _explanation(
                rec.model_dump_json(), inv.model_dump_json(), prefs.model_dump_json(),
                cat.model_dump_json())
        except AIUnavailable as exc:
            st.error(str(exc))
    if text := st.session_state.get(f"explained_{cat.id}"):
        with st.container(border=True):
            st.markdown(text)
            st.caption("Written by Claude from the tool's results.")


# Charts -----------------------------------------------------------------------
# Colours come from ui/theme.py, in light and dark steps.


def _palette() -> dict:
    theme = getattr(getattr(st, "context", None), "theme", None)
    return PALETTE["dark" if getattr(theme, "type", "light") == "dark" else "light"]


def _value_chart(points: list, base: str) -> alt.Chart:
    pal = _palette()
    wide = pd.DataFrame([{"Day": pd.Timestamp(p.day), "Value": float(p.value),
                          "Money invested": float(p.invested)} for p in points])
    long = wide.melt("Day", var_name="Series", value_name="Amount")
    series = ["Value", "Money invested"]
    long_range = (points[-1].day - points[0].day).days > 180
    x = alt.X("Day:T", title=None, axis=alt.Axis(
        grid=False, labelColor=pal["muted"], format="%b %Y" if long_range else "%d %b",
        tickCount=6))
    y = alt.Y("Amount:Q", title=None, axis=alt.Axis(format=",.0f", gridColor=pal["grid"],
                                                     labelColor=pal["muted"], tickCount=5))
    color = alt.Color("Series:N", scale=alt.Scale(domain=series, range=pal["series"]),
                      legend=alt.Legend(orient="top", title=None))
    nearest = alt.selection_point(nearest=True, on="pointerover", fields=["Day"], empty=False)
    lines = alt.Chart(long).mark_line(strokeWidth=2).encode(x=x, y=y, color=color)
    # Invisible full-height rules are the hover targets and carry the tooltip.
    targets = alt.Chart(wide).mark_rule(opacity=0, strokeWidth=12).encode(
        x="Day:T",
        tooltip=[alt.Tooltip("Day:T", title="Date", format="%d %b %Y"),
                 alt.Tooltip("Value:Q", title=f"Value ({base})", format=",.0f"),
                 alt.Tooltip("Money invested:Q", title=f"Invested ({base})", format=",.0f")],
    ).add_params(nearest)
    crosshair = alt.Chart(wide).mark_rule(color=pal["muted"], strokeWidth=1).encode(
        x="Day:T").transform_filter(nearest)
    dots = lines.mark_point(filled=True, size=64, opacity=1).encode(
        opacity=alt.condition(nearest, alt.value(1), alt.value(0)))
    return alt.layer(lines, crosshair, dots, targets).properties(height=320)


def _bar_chart(slices: list, base: str) -> alt.Chart:
    pal = _palette()
    df = pd.DataFrame([{"Label": s.label, "Value": float(s.value), "Share": float(s.pct),
                        "Share label": f"{s.pct:g}%"} for s in slices])
    order = list(df["Label"])
    y = alt.Y("Label:N", sort=order, title=None, axis=alt.Axis(labelLimit=140, ticks=False))
    bars = alt.Chart(df).mark_bar(color=pal["series"][0], cornerRadiusEnd=4, height=18).encode(
        x=alt.X("Value:Q", title=None, axis=alt.Axis(format=",.0f", gridColor=pal["grid"],
                                                     labelColor=pal["muted"], tickCount=4)),
        y=y,
        tooltip=[alt.Tooltip("Label:N", title=" "),
                 alt.Tooltip("Value:Q", title=f"Value ({base})", format=",.0f"),
                 alt.Tooltip("Share:Q", title="Share (%)", format=".1f")],
    )
    labels = bars.mark_text(align="left", dx=6, color=pal["muted"]).encode(
        text="Share label:N")
    return alt.layer(bars, labels).properties(height=max(120, 32 * len(df)))


# Dashboard --------------------------------------------------------------------

PERIODS = {"1 month": 31, "6 months": 183, "1 year": 365, "3 years": 3 * 365, "All": None}


@st.cache_data(ttl=6 * 60 * 60, show_spinner="Fetching price history…")
def _history(symbols: tuple[tuple[str, str], ...], currencies: tuple[str, ...], base: str,
             start: date, end: date):
    history = CachedHistory(get_provider(), SupabasePriceStore(_client()))
    prices = {s: history.history(*s, start, end) for s in symbols}
    fx = {c: history.fx_history(base, c, start, end) for c in currencies}
    return prices, fx


def _dashboard_page(repo: PortfolioRepository) -> None:
    holdings = repo.list_holdings()
    accounts = repo.list_cash_accounts()
    if not holdings and not accounts:
        st.info("Nothing here yet. Add holdings on the Holdings page, cash on the Cash page, or "
                "import a broker export on the Import page.")
        return
    categories = repo.list_categories()
    profile = repo.get_profile()
    base = profile.base_currency if profile else "AUD"
    values = valued_holdings(holdings, base, _quotes, _fx_rates)
    cash = valued_cash(accounts, base, _fx_rates)
    value, cost = total(values, cash)
    gain = value - cost  # cash counts in both, so it doesn't change the gain

    today = date.today()
    dividends = repo.list_dividends()
    div_fx = _fx_rates(base, tuple(sorted({d.currency for d in dividends} - {base})))
    year_income = income_in_base(dividends, div_fx, base, since=today - timedelta(days=365))
    _metric_rows([("Portfolio value", _money(value, base)),
                  ("Money invested", _money(cost, base)),
                  ("Gain", _money(gain, base), f"{gain / cost * 100:.1f}%" if cost else None),
                  ("Dividends, last 12 months", _money(year_income, base))])
    if dividends and cost:
        all_income = income_in_base(dividends, div_fx, base)
        st.caption(f"Including all {_money(all_income, base)} of dividends recorded, your total "
                   f"return is {_money(gain + all_income, base)} "
                   f"({(gain + all_income) / cost * 100:.1f}% of money invested).")
    _price_message([v.priced for v in values])

    st.subheader("Value over time")
    period = st.radio("Period", list(PERIODS), index=2, horizontal=True, key="dash_period",
                      label_visibility="collapsed")
    first_buy = min([h.buy_date for h in holdings] + [a.as_of for a in accounts])
    days = PERIODS[period]
    start = max(first_buy, today - timedelta(days=days)) if days else first_buy
    points = portfolio_history(holdings, base, start, today, _history, cash=accounts)
    if len(points) > 1:
        st.altair_chart(_value_chart(points, base), use_container_width=True)
        with st.expander("Show as a table"):
            st.dataframe(pd.DataFrame([{"Date": p.day, f"Value ({base})": float(p.value),
                                        f"Money invested ({base})": float(p.invested)}
                                       for p in points]), hide_index=True)
        st.caption("Money invested is what you paid, including fees, at the exchange rate on "
                   "the day. Value uses each day's closing price."
                   + (" Cash accounts have no balance history, so each shows as a flat line "
                      "from the date its balance was last updated." if accounts else ""))
    else:
        st.caption("Not enough history yet to draw a chart.")

    st.subheader("Where your money is")
    by = st.radio("Break down by", list(Dimension), horizontal=True, key="dash_by",
                  format_func=lambda d: d.value)
    slices = breakdown(values, categories, by, cash=cash)
    st.altair_chart(_bar_chart(slices, base), use_container_width=True)
    with st.expander("Show as a table"):
        st.dataframe(pd.DataFrame([{by.value: s.label, f"Value ({base})": float(s.value),
                                    "Share (%)": float(s.pct)} for s in slices]),
                     hide_index=True)

    st.subheader("Progress towards your goals")
    for cat in categories:
        in_goal = [v for v in values if v.holding.category_id == cat.id]
        p = goal_progress(cat, in_goal, today,
                          [c for c in cash if c.account.category_id == cat.id])
        with st.container(border=True):
            st.markdown(f"**{cat.name}**")
            if p.target:
                st.progress(min(float(p.value / p.target), 1.0),
                            text=f"{_money(p.value, base)} of {_money(p.target, base)} "
                                 f"({p.pct_of_target:g}%)")
            else:
                st.markdown(f"{_money(p.value, base)}. Set a target amount on the Categories "
                            "page to track progress.")
            if p.on_track is True:
                st.caption(f"✅ On track: about {_money(p.projected, base)} by "
                           f"{cat.target_date:%b %Y} at an assumed "
                           f"{p.assumed_return * 100:g}% a year.")
            elif p.on_track is False:
                st.caption(f"⚠️ Behind: about {_money(p.projected, base)} by "
                           f"{cat.target_date:%b %Y} at an assumed {p.assumed_return * 100:g}% "
                           f"a year. Adding about {_money(p.monthly_needed, base)} a month "
                           "would close the gap.")
    st.caption("Projections assume steady returns of 7% a year for shares and 3.5% for cash "
               "funds, with savings accounts at their own interest rate, before fees and tax. "
               "Real returns vary a lot from year to year; this is an illustration, not a "
               "forecast.")


# Health check -----------------------------------------------------------------

def _health_page(repo: PortfolioRepository) -> None:
    holdings = repo.list_holdings()
    accounts = repo.list_cash_accounts()
    if not holdings and not accounts:
        st.info("No holdings or cash yet, so there's nothing to check.")
        return
    categories = repo.list_categories()
    profile = repo.get_profile()
    base = profile.base_currency if profile else "AUD"
    goal_prefs = {c.id: repo.get_category_preferences(c.id) for c in categories}
    with st.spinner("Running the health check…"):
        report = health_report(categories, holdings, repo.get_preferences(), goal_prefs, base,
                               _quotes, _fx_rates, _fundamentals, cash_accounts=accounts)

    st.caption("Checks each goal against its target mix and deadline, and the whole portfolio "
               "for concentration, fees and missing data. Fixes are always about where new "
               "money goes: nothing here suggests selling.")
    _metric_rows([(SEVERITY_BADGE[sev], report.count(sev)) for sev in SEVERITY_ORDER])

    areas = list(dict.fromkeys(["Portfolio"] + [c.name for c in categories]
                               + [p.platform for p in report.platforms]))
    for area in areas:
        found = [f for f in report.findings if f.area == area]
        if not found:
            continue
        kind = ("Goal" if area in {c.name for c in categories} else
                "Platform" if area != "Portfolio" else "")
        st.subheader(f"{kind}: {area}" if kind else area)
        for f in found:
            with st.container(border=True):
                st.markdown(f"**{SEVERITY_BADGE[f.severity]} · {f.title}**")
                st.markdown(f.detail)
                if f.action:
                    st.caption(f"What to do: {f.action}")

    st.subheader("By platform")
    df = pd.DataFrame([
        {"Platform": p.platform, f"Value ({base})": float(p.value), "Share (%)": float(p.pct),
         "Holdings": p.holdings, f"Invested ({base})": float(p.invested),
         f"Brokerage paid ({base})": float(p.fees), "Brokerage (% of invested)": float(p.fees_pct)}
        for p in report.platforms
    ])
    st.dataframe(df[["Platform", f"Value ({base})", "Share (%)"]], hide_index=True)
    with st.expander("All columns"):
        st.dataframe(df, hide_index=True)


# Import -----------------------------------------------------------------------

NONE = "(none)"
LAYOUT_NAMES = {
    Layout.COLUMNS: "Transactions, one column per field (CMC Markets and most others)",
    Layout.COMMSEC: "CommSec transactions (Details column like 'B 100 VAS @ 95.50')",
    Layout.SNAPSHOT: "Holdings snapshot, one row per stock (CMC Markets profit and loss)",
}
LAYOUT_FIELDS = {
    Layout.COLUMNS: ["date", "side", "ticker", "exchange", "units", "price", "fees", "value"],
    Layout.COMMSEC: ["date", "details", "value", "fees"],
    Layout.SNAPSHOT: ["ticker", "exchange", "units", "cost", "price", "fx"],
}
FIELD_NAMES = {"fx": "FX rate", "units": "Units", "price": "Average price (if no cost)",
               "ticker": "Code", "cost": "Total cost"}


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _import_page(repo: PortfolioRepository) -> None:
    ss = st.session_state
    run = ss.setdefault("import_run", 0)
    st.caption("Import purchases from a CommSec or CMC Markets export: transaction history, or "
               "a holdings snapshot such as CMC's profit and loss export. You'll see exactly "
               "what will be added before anything is saved.")
    upload = st.file_uploader("Broker export (CSV)", type=["csv"], key=f"import_file_{run}")
    pasted = st.text_area("…or paste the file's contents", key=f"import_text_{run}", height=100)
    text = _decode(upload.getvalue()) if upload else pasted
    if not text.strip():
        return

    header, rows, header_line = read_csv(text)
    if not header or not rows:
        st.error("Couldn't find a header row and data in that file.")
        return
    mapping = detect(header)
    broker = guess_broker(mapping, upload.name if upload else None)

    categories = repo.list_categories()
    holdings = repo.list_holdings()
    profile = repo.get_profile()
    default_platform = profile.platform_for_new if profile else FALLBACK_PLATFORM
    platforms = sorted(set(_platform_options(holdings, default_platform))
                       | {Broker.COMMSEC.value, Broker.CMC.value})
    c1, c2 = st.columns(2)
    platform = _platform_input(c1, platforms, broker.value if broker is not Broker.OTHER
                               else default_platform, f"import_platform_{run}")
    goal_names = [c.name for c in categories] + [UNCATEGORISED]
    goal = c2.selectbox("Goal for these holdings", goal_names, key=f"import_goal_{run}",
                        help="You can change it row by row below.")
    exchanges = list(EXCHANGES)
    default_exchange = st.selectbox(
        "Exchange, when the file doesn't say", exchanges, key=f"import_exchange_{run}",
        index=exchanges.index(get_settings().default_exchange.upper())
        if get_settings().default_exchange.upper() in exchanges else 0)

    with st.expander(f"Columns ({LAYOUT_NAMES[mapping.layout]})",
                     expanded=bool(mapping.missing())):
        layouts = list(Layout)
        layout = st.radio("Layout", layouts, index=layouts.index(mapping.layout),
                          key=f"import_layout_{run}", format_func=LAYOUT_NAMES.get)
        options = [NONE] + header
        cols = st.columns(2)
        chosen = {}
        for i, f in enumerate(LAYOUT_FIELDS[layout]):
            current = mapping.columns.get(f)
            pick = cols[i % 2].selectbox(FIELD_NAMES.get(f, f.capitalize()), options,
                                         key=f"import_col_{f}_{run}",
                                         index=options.index(current) if current in options
                                         else 0)
            chosen[f] = None if pick == NONE else pick
        mapping = Mapping(layout, chosen, broker)
    if missing := mapping.missing():
        st.error(f"Choose the column for: {', '.join(missing)}.")
        return

    as_at = None
    if mapping.layout is Layout.SNAPSHOT:
        on_platform = [h.buy_date for h in holdings if h.platform == platform]
        st.info("This file lists what you hold, without purchase dates. Import your transaction "
                "history first: then this adds only the units it doesn't cover, as one parcel "
                "per stock dated below and priced so the total cost matches the broker's.")
        as_at = st.date_input(
            "Date for holdings not in your transaction history", key=f"import_as_at_{run}",
            value=min(on_platform) if on_platform else date.today(), max_value=date.today(),
            format="DD/MM/YYYY",
            help="Use the earliest date your transaction history reaches. It defaults to your "
                 "earliest purchase already imported for this platform.")

    goal_ids = {c.name: c.id for c in categories} | {UNCATEGORISED: None}
    parsed = parse_rows(rows, mapping, default_exchange=default_exchange, platform=platform,
                        category_id=goal_ids[goal], existing=holdings, header_line=header_line,
                        as_at=as_at)

    def status(r) -> str:
        if r.importable:
            text = "Already in your holdings" if r.duplicate else "Ready to import"
            return f"{text}. {r.note}" if r.note else text
        return r.problem or r.note or "Skipped"

    def cell(r, name):
        source = r.holding.model_dump() if r.holding else (r.partial or {})
        return source.get(name)

    def number(value):
        return float(value) if value is not None else None

    df = pd.DataFrame([{
        "Import": r.importable and not r.duplicate,
        "Line": r.line,
        "Ticker": cell(r, "ticker") or "",
        "Exchange": cell(r, "exchange") or "",
        "Ccy": EXCHANGES[cell(r, "exchange")].currency if cell(r, "exchange") else "",
        "Date": cell(r, "buy_date"),
        "Units": number(cell(r, "units")),
        "Price": number(cell(r, "buy_price")),
        "Fees": number(cell(r, "fees")),
        "Cost": (float(r.holding.cost_base) if r.holding else None),
        "Goal": goal,
        "Status": status(r),
    } for r in parsed])
    buys = [r for r in parsed if r.importable]
    sells = [r for r in parsed if r.side == "sell"]
    covered = [r for r in parsed if r.side == "covered"]
    needs_price = [r for r in parsed if r.partial]
    st.markdown(f"Found **{len(buys)} purchase(s)** ready"
                + (f", of which {sum(r.duplicate for r in buys)} are already in your holdings"
                   if any(r.duplicate for r in buys) else "")
                + (f", **{len(needs_price)}** needing a price" if needs_price else "")
                + (f", {len(covered)} already covered" if covered else "")
                + (f", and **{len(sells)} sale(s)**" if sells else "")
                + f", in {len(parsed)} rows.")
    edited = st.data_editor(
        df, hide_index=True, key=f"import_rows_{run}",
        disabled=[c for c in df.columns if c not in {"Import", "Goal", "Price"}],
        column_config={
            "Goal": st.column_config.SelectboxColumn(options=goal_names, required=True),
            "Date": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Price": st.column_config.NumberColumn(
                format="%.4f", min_value=0,
                help="Per unit, in the exchange's currency (Ccy). You can change it."),
            "Fees": st.column_config.NumberColumn(format="%.2f"),
            "Cost": st.column_config.NumberColumn(
                format="%.2f", help="Units x price + fees, in the exchange's currency"),
            "Status": st.column_config.TextColumn(width="large"),
        })
    if needs_price:
        st.caption("For rows with no cost, enter the cost per unit in Price and tick Import. For "
                   "demerger shares, the cost base is usually a share of the original holding's "
                   "cost; check the company's or the ATO's guidance.")
    if sells:
        st.caption("Sales aren't imported, because holdings are recorded purchase by purchase. "
                   "After importing, reduce or delete the parcels you've sold on the Holdings "
                   "page.")

    chosen_rows = []
    for r, (_, e) in zip(parsed, edited.iterrows(), strict=True):
        if not e["Import"] or pd.isna(e["Price"]) or e["Price"] <= 0:
            continue
        price = Decimal(str(e["Price"]))
        if r.importable:
            chosen_rows.append((r.holding.model_copy(update={"buy_price": price}), e["Goal"]))
        elif r.partial:
            chosen_rows.append((HoldingIn(**r.partial, buy_price=price), e["Goal"]))
    if st.button(f"Import {len(chosen_rows)} holding(s)", type="primary",
                 disabled=not chosen_rows, key=f"import_go_{run}"):
        for holding, g in chosen_rows:
            repo.create_holding(holding.model_copy(update={"category_id": goal_ids.get(g)}))
        ss.import_run = run + 1  # fresh, empty widgets
        st.toast(f"Imported {len(chosen_rows)} holding(s)")
        st.rerun()


# Dividends --------------------------------------------------------------------

_HOLDING_OPTION = re.compile(r"^\s*([A-Za-z0-9.]+)\s*(?:\((\w+)\))?\s*$")


def _dividend_form(holdings: list[Holding], platforms: list[str], default_platform: str
                   ) -> DividendIn | None:
    held = sorted({f"{h.ticker} ({h.exchange})" for h in holdings})
    with st.form("add_dividend", clear_on_submit=True):
        c1, c2 = st.columns(2)
        holding = c1.selectbox("Holding", held, index=None, accept_new_options=True,
                               placeholder="Choose, or type a code like VAS (ASX)")
        pay_date = c2.date_input("Pay date", value=date.today(), max_value=date.today(),
                                 format="DD/MM/YYYY")
        c3, c4 = st.columns(2)
        amount = c3.number_input("Amount paid", min_value=0.0, step=10.0, format="%.2f",
                                 help="Cash before tax, in the holding's currency. For a "
                                      "reinvested dividend, the amount reinvested.")
        franking = c4.number_input("Franking credits", min_value=0.0, step=1.0, format="%.2f",
                                   help="From the dividend statement. Australian shares only.")
        c5, c6 = st.columns(2)
        withholding = c5.number_input("Foreign tax withheld", min_value=0.0, step=1.0,
                                      format="%.2f", help="For example US tax on US shares.")
        platform = _platform_input(c6, platforms, default_platform, "dividend_platform")
        reinvested = st.checkbox("Reinvested (DRP)",
                                 help="If ticked, also add the new units on the Holdings page.")
        notes = st.text_input("Notes")
        submitted = st.form_submit_button("Add dividend", type="primary")
    if not submitted:
        return None
    m = _HOLDING_OPTION.match(holding or "")
    if not m:
        st.error("Choose a holding, or type a code like VAS (ASX).")
        return None
    try:
        return DividendIn(
            ticker=m.group(1), exchange=m.group(2) or get_settings().default_exchange,
            pay_date=pay_date, amount=Decimal(str(amount)),
            franking_credits=Decimal(str(franking)), withholding_tax=Decimal(str(withholding)),
            reinvested=reinvested, platform=platform, notes=notes or None)
    except ValidationError as exc:
        _show_errors(exc)
        return None


def _dividends_page(repo: PortfolioRepository) -> None:
    st.caption("Record dividends and distributions so your returns include income, not just "
               "price changes. Totals are by Australian financial year (1 July to 30 June).")
    holdings = repo.list_holdings()
    profile = repo.get_profile()
    default_platform = profile.platform_for_new if profile else FALLBACK_PLATFORM
    platforms = _platform_options(holdings, default_platform)
    st.subheader("Record a dividend")
    new = _dividend_form(holdings, platforms, default_platform)
    if new:
        repo.create_dividend(new)
        st.toast(f"Added {new.amount:,.2f} {new.currency} from {new.ticker}")
        st.rerun()

    dividends = repo.list_dividends()
    if not dividends:
        st.info("No dividends recorded yet.")
        return

    st.subheader("By financial year")
    money = st.column_config.NumberColumn(format="%.2f")
    st.dataframe(pd.DataFrame([
        {"Financial year": y.year, "Ccy": y.currency, "Payments": y.payments,
         "Cash": float(y.cash), "Franking credits": float(y.franking),
         "Grossed up": float(y.grossed_up), "Foreign tax withheld": float(y.withholding),
         "Reinvested": float(y.reinvested)}
        for y in by_financial_year(dividends)
    ]), hide_index=True, column_config={c: money for c in [
        "Cash", "Franking credits", "Grossed up", "Foreign tax withheld", "Reinvested"]})
    st.caption("Grossed up is cash plus franking credits, the amount that counts as income for "
               "tax. Check figures against your statements; this isn't tax advice.")

    st.subheader(f"Payments ({len(dividends)})")
    event = st.dataframe(pd.DataFrame([
        {"Pay date": d.pay_date, "Ticker": d.ticker, "Exchange": d.exchange,
         "Amount": float(d.amount), "Ccy": d.currency, "Franking": float(d.franking_credits),
         "Tax withheld": float(d.withholding_tax), "DRP": d.reinvested,
         "Platform": d.platform or "", "Notes": d.notes or ""}
        for d in dividends
    ]), hide_index=True, on_select="rerun", selection_mode="single-row",
        column_config={"Pay date": st.column_config.DateColumn(format="DD/MM/YYYY"),
                       "Amount": money, "Franking": money, "Tax withheld": money})
    if not event.selection.rows:
        st.caption("Select a row to delete it.")
        return
    selected = dividends[event.selection.rows[0]]
    confirm = st.checkbox(f"Yes, delete the {selected.ticker} dividend paid "
                          f"{selected.pay_date:%d/%m/%Y}", key=f"del_div_ok_{selected.id}")
    if st.button("Delete dividend", disabled=not confirm, key=f"del_div_{selected.id}"):
        repo.delete_dividend(selected.id)
        st.toast(f"Deleted {selected.ticker} dividend")
        st.rerun()


# Notifications ----------------------------------------------------------------

def _notifications_page(repo: PortfolioRepository) -> None:
    st.caption("Updates about new or changed recommendations, new health check issues, big "
               "price moves and goal deadlines. They're checked after the ASX closes, as often "
               "as you choose on the Profile page.")
    items = repo.list_notifications()
    names = {str(c.id): c.name for c in repo.list_categories()}
    unread = [n for n in items if not n.get("read_at")]
    if unread and st.button(f"Mark all {len(unread)} as read"):
        repo.mark_all_notifications_read()
        st.rerun()
    if not items:
        st.info("No notifications yet. The first check after you sign up only records where "
                "things stand; later checks tell you what changed.")
    for n in items:
        goal = names.get(str(n.get("category_id")), "Portfolio") if n.get("category_id") \
            else "Portfolio"
        when = pd.Timestamp(n["created_at"]).tz_convert(None)
        with st.container(border=True):
            new = "" if n.get("read_at") else "🔵 **New** · "
            st.markdown(f"{new}**{goal}: {n['title']}**")
            st.markdown(n["body"])
            st.caption(f"{when:%d/%m/%Y %H:%M}" + (" · emailed" if n.get("emailed_at") else ""))
            c1, c2 = st.columns(2)
            if not n.get("read_at") and c1.button("Mark as read", key=f"read_{n['id']}"):
                repo.mark_notification_read(n["id"])
                st.rerun()
            if c2.button("Delete", key=f"delete_note_{n['id']}"):
                repo.delete_notification(n["id"])
                st.rerun()

    st.divider()
    if st.button("Check for changes now",
                 help="Compares your recommendations with the last check, without saving or "
                      "emailing anything."):
        _preview_changes(repo)


def _preview_changes(repo: PortfolioRepository) -> None:
    categories = repo.list_categories()
    profile = repo.get_profile()
    with st.spinner("Checking your recommendations…"):
        outcome = build_notifications(
            categories=categories,
            goal_prefs={c.id: repo.get_category_preferences(c.id) for c in categories},
            holdings=repo.list_holdings(), cash_accounts=repo.list_cash_accounts(),
            inv=repo.get_preferences(), base=profile.base_currency if profile else "AUD",
            old_snapshots=repo.get_snapshots(), quotes_fn=_quotes, fx_fn=_fx_rates,
            fundamentals_fn=_fundamentals, history_fn=_history, today=date.today(),
            weekly=bool(profile and profile.notify_frequency == "weekly"))
    if outcome.baseline:
        st.info("There's no earlier check to compare with yet. The first scheduled check will "
                "record where things stand.")
    elif not outcome.messages:
        st.success("Nothing new since the last check.")
    else:
        st.markdown("**The next notification would say:**")
        for m in outcome.messages:
            st.markdown(f"- **{m.goal or 'Portfolio'}: {m.title}**: {m.body}")


# Profile ----------------------------------------------------------------------
# Profile ----------------------------------------------------------------------

def _profile_page(repo: PortfolioRepository, email: str) -> None:
    profile = repo.get_profile()
    if profile is None:
        st.error("Profile not found. Was the database migration run before you signed up?")
        return
    with st.form("profile"):
        st.text_input("Email", value=email, disabled=True)
        name = st.text_input("Display name", value=profile.display_name or "")
        currencies = sorted({e.currency for e in EXCHANGES.values()})
        ccy = st.selectbox("Base currency", currencies,
                           index=currencies.index(profile.base_currency)
                           if profile.base_currency in currencies else 0)
        platform = st.text_input(
            "Default platform for new holdings", value=profile.default_platform or "",
            placeholder=FALLBACK_PLATFORM, max_chars=50, key="profile_default_platform",
            help=f"Preselected when you add or import holdings. Leave blank for "
                 f"{FALLBACK_PLATFORM}.")
        st.markdown("**Notifications**")
        frequencies = list(FREQUENCY_LABELS)
        frequency = st.selectbox("How often", frequencies, key="profile_frequency",
                                 index=frequencies.index(profile.notify_frequency),
                                 format_func=FREQUENCY_LABELS.get,
                                 help="Checked after the ASX closes. Off stops them entirely.")
        notify_email = st.checkbox("Email me about new recommendations", key="profile_email",
                                   value=profile.notify_email,
                                   help="Otherwise they only appear on the Notifications page.")
        if st.form_submit_button("Save", type="primary"):
            try:
                repo.update_profile(ProfileIn(display_name=name, base_currency=ccy,
                                              default_platform=platform,
                                              notify_email=notify_email,
                                              notify_frequency=frequency))
                st.toast("Profile saved")
                st.rerun()
            except ValidationError as exc:
                _show_errors(exc)


# Main -------------------------------------------------------------------------

def main() -> None:
    try:
        user = _signed_in_user()
    except SupabaseNotConfigured as exc:
        st.error(str(exc))
        st.stop()

    if user is None:
        _login_screen()
        return

    repo = PortfolioRepository(_client())
    profile = repo.get_profile()
    with st.sidebar:
        st.markdown("## 📈 Share Recommender")
        st.caption(f"Signed in as {(profile and profile.display_name) or user.email}")
        if st.button("Sign out"):
            _client().auth.sign_out()
            st.session_state.clear()
            st.rerun()

    unread = st.session_state["unread"] = _unread(repo)
    NAV.clear()
    for name, (icon, render) in PAGES.items():
        title = f"{name} ({unread})" if name == "Notifications" and unread else name
        NAV[name] = st.Page(_page(name, render, repo, user.email), title=title, icon=icon,
                            url_path=name.lower().replace(" ", "-"),
                            default=name == "Dashboard")
    nav = st.navigation(list(NAV.values()), position="top")
    # Tests choose a page through session state; people use the menu or the page's URL.
    wanted = st.session_state.pop("nav_page", None)
    if wanted and NAV[wanted] is not nav:
        st.switch_page(NAV[wanted])
    nav.run()


NAV: dict = {}  # page name -> st.Page, rebuilt on every run


def _page(name: str, render, repo: PortfolioRepository, email: str):
    """A page for st.navigation: title, the page itself, friendly errors and the footer."""
    def run() -> None:
        st.title(name)
        try:
            if render is None:
                _profile_page(repo, email)
            else:
                render(repo)
        except APIError as exc:
            if exc.code not in MISSING_SCHEMA:
                raise
            st.error("The database is missing an update. Run any migrations in "
                     "supabase/migrations/ you haven't run yet (see docs/supabase-setup.md), "
                     "then reload.")
        if name in FOOTER_PAGES:
            st.divider()
            st.caption(DISCLAIMER)

    run.__name__ = f"page_{name.lower().replace(' ', '_')}"
    return run


MISSING_SCHEMA = {"42703", "42P01", "PGRST204", "PGRST205"}  # a missing column or table
FOOTER_PAGES = {"Dashboard", "Recommendations", "Health check", "Notifications"}


def _price_message(priced: list[bool]) -> None:
    """Say so when prices are missing, rather than showing blanks without a reason."""
    if priced and not any(priced):
        st.warning("Couldn't get any prices from Yahoo Finance just now, so holdings are shown "
                   "at what you paid. Try ↻ Refresh prices on the Holdings page in a few "
                   "minutes.")
    elif not all(priced):
        st.caption("Some holdings have no current price, so they're counted at what you paid. "
                   "Check the ticker and exchange if it doesn't come back.")


FREQUENCY_LABELS = {"off": "Off", "daily": "Daily on weekdays", "weekly": "Weekly on Mondays"}


def _unread(repo: PortfolioRepository) -> int:
    try:
        return repo.unread_count()
    except APIError:  # the notifications migration hasn't been run yet
        return 0


PAGES = {  # name -> (icon, page function); Profile also needs the email
    "Dashboard": (":material/dashboard:", _dashboard_page),
    "Holdings": (":material/list_alt:", _holdings_page),
    "Cash": (":material/savings:", _cash_page),
    "Import": (":material/upload_file:", _import_page),
    "Categories": (":material/category:", _categories_page),
    "Preferences": (":material/tune:", _preferences_page),
    "Recommendations": (":material/lightbulb:", _recommendations_page),
    "Dividends": (":material/payments:", _dividends_page),
    "Health check": (":material/health_and_safety:", _health_page),
    "Notifications": (":material/notifications:", _notifications_page),
    "Profile": (":material/person:", None),
}


main()
