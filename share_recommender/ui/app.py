"""Streamlit UI (MVP). Run with:  streamlit run share_recommender/ui/app.py

The UI only talks to the repository, core and data packages, so it can be
replaced by a web or mobile front end on top of the FastAPI app later.
"""

from datetime import date
from decimal import Decimal

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from share_recommender.config import get_settings
from share_recommender.core.category_templates import CATEGORY_TEMPLATES
from share_recommender.core.exchanges import EXCHANGES
from share_recommender.core.models import Category, CategoryIn, Holding, HoldingIn, RiskLevel
from share_recommender.core.portfolio import totals_by_currency, value_holdings
from share_recommender.data import Quote, get_provider
from share_recommender.db.repository import PortfolioRepository
from share_recommender.db.supabase_client import SupabaseNotConfigured, anon_client
from supabase import Client

COMMON_PLATFORMS = ["CMC Markets"]

st.set_page_config(page_title="Share Recommender", page_icon="📈", layout="wide")


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


# Holdings ---------------------------------------------------------------------

def _holding_form(key: str, categories: list[Category], platforms: list[str],
                  existing: Holding | None = None) -> HoldingIn | None:
    """Render the add/edit form. Returns validated input when submitted, else None."""
    settings = get_settings()
    exchanges = list(EXCHANGES)
    cat_ids = [c.id for c in categories] + [None]
    cat_names = {c.id: c.name for c in categories} | {None: "Uncategorised"}
    default_exchange = existing.exchange if existing else settings.default_exchange.upper()
    default_platform = existing.platform if existing else (platforms[0] if platforms else None)

    with st.form(key, clear_on_submit=existing is None):
        c1, c2, c3 = st.columns(3)
        ticker = c1.text_input("Ticker", value=existing.ticker if existing else "",
                               placeholder="e.g. VAS")
        exchange = c2.selectbox(
            "Exchange", exchanges,
            index=exchanges.index(default_exchange) if default_exchange in exchanges else 0,
            format_func=lambda code: f"{code} ({EXCHANGES[code].currency})",
        )
        category_id = c3.selectbox(
            "Category", cat_ids,
            index=cat_ids.index(existing.category_id) if existing else 0,
            format_func=lambda cid: cat_names[cid],
        )
        c4, c5, c6, c7 = st.columns(4)
        units = c4.number_input("Units", min_value=0.0, step=1.0, format="%.4f",
                                value=float(existing.units) if existing else 0.0)
        buy_price = c5.number_input("Buy price per unit", min_value=0.0, step=0.01, format="%.4f",
                                    value=float(existing.buy_price) if existing else 0.0)
        fees = c6.number_input("Fees / brokerage", min_value=0.0, step=1.0, format="%.2f",
                               value=float(existing.fees) if existing else 0.0)
        buy_date = c7.date_input("Buy date", value=existing.buy_date if existing else date.today(),
                                 max_value=date.today(), format="DD/MM/YYYY")
        c8, c9 = st.columns([1, 2])
        platform = c8.selectbox(
            "Platform", platforms,
            index=platforms.index(default_platform) if default_platform in platforms else None,
            accept_new_options=True, placeholder="Choose or type a platform",
        )
        notes = c9.text_input("Notes", value=(existing.notes or "") if existing else "")
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
    platforms = sorted({h.platform for h in holdings if h.platform} | set(COMMON_PLATFORMS))

    st.subheader("Add a holding")
    st.caption("Enter each purchase separately. Prices are in the exchange's currency.")
    new = _holding_form("add_holding", categories, platforms)
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
        col.metric(f"Market value ({ccy})", f"{t['market_value']:,.2f}",
                   delta=f"{gain:,.2f}" if t["priced_cost_base"] else None)

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
    event = st.dataframe(
        df, hide_index=True,
        on_select="rerun", selection_mode="single-row",
        column_config={
            "Units": st.column_config.NumberColumn(format="%.4f"),
            "Buy price": money, "Fees": money, "Cost base": money, "Last price": money,
            "Market value": money, "Gain": money,
            "Gain %": st.column_config.NumberColumn(format="%.1f%%"),
            "Buy date": st.column_config.DateColumn(format="DD/MM/YYYY"),
        },
    )
    if any(v.last_price is None for v in valuations):
        st.caption("Blank prices mean the data provider has no price for that ticker and exchange.")

    rows = event.selection.rows
    if not rows:
        st.caption("Select a row to edit or delete it.")
        return

    selected = holdings[rows[0]]
    st.subheader(f"Edit {selected.ticker} bought {selected.buy_date:%d/%m/%Y}")
    updated = _holding_form(f"edit_{selected.id}", categories, platforms, existing=selected)
    if updated:
        repo.update_holding(selected.id, updated)
        st.toast(f"Updated {updated.ticker}")
        st.rerun()

    confirm = st.checkbox("Yes, I want to delete this holding", key=f"confirm_{selected.id}")
    if st.button("Delete holding", disabled=not confirm, key=f"delete_{selected.id}"):
        repo.delete_holding(selected.id)
        st.toast(f"Deleted {selected.ticker}")
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
        cols = st.columns(len(templates))
        for col, t in zip(cols, templates, strict=True):
            if col.button(f"＋ {t['name']}", key=f"tpl_{t['name']}", help=t["description"]):
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
        if st.form_submit_button("Save", type="primary"):
            repo.update_profile(name or None, ccy)
            st.toast("Profile saved")
            st.rerun()


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

        page = st.radio("Go to", ["Holdings", "Categories", "Profile"], key="page")

    st.title(page)
    if page == "Holdings":
        _holdings_page(repo)
    elif page == "Categories":
        _categories_page(repo)
    else:
        _profile_page(repo, user.email)


main()
