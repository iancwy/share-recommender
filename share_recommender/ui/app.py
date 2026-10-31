"""Streamlit UI (MVP). Run with:  streamlit run share_recommender/ui/app.py

The UI only talks to the repository, core and data packages, so it can be
replaced by a web or mobile front end on top of the FastAPI app later.
"""

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
from share_recommender.core.health import SEVERITY_ORDER, Severity
from share_recommender.core.importer import (
    Broker,
    Layout,
    Mapping,
    detect,
    guess_broker,
    parse_rows,
    read_csv,
)
from share_recommender.core.models import (
    FALLBACK_PLATFORM,
    Category,
    CategoryIn,
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
from share_recommender.portfolio_service import (
    health_report,
    portfolio_history,
    total,
    valued_holdings,
)
from share_recommender.recommendations import build_recommendation
from supabase import Client

DISCLAIMER = ("General information from a rules-based tool, not personal financial advice. "
              "Check any investment's fees and documents before you buy.")

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
        platform = _platform_input(c8, platforms, current_platform, f"{key}_platform")
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
    st.dataframe(
        df, hide_index=True,
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
        st.button("Go to Preferences", type="primary",
                  on_click=lambda: st.session_state.update(page="Preferences"))
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
    rec = build_recommendation(cat, inv, prefs,
                               [h for h in all_holdings if h.category_id == cat.id], base,
                               _quotes, _fx_rates, _fundamentals,
                               amount=Decimal(str(amount)) if amount else None,
                               all_holdings=all_holdings)
    mix, gaps = rec.mix, rec.gaps
    st.caption(DISCLAIMER)

    m1, m2, m3, m4 = st.columns(4)
    if mix.speculative:
        m1.metric("Goal type", "Speculative")
        m2.metric("Investments", len(mix.picks))
    else:
        m1.metric("Shares (growth)", f"{mix.growth_pct}%")
        m2.metric("Cash (defensive)", f"{100 - mix.growth_pct}%")
    m3.metric("Time horizon", f"{mix.horizon_years:g} years")
    m4.metric(f"Held in this goal ({base})", f"{gaps.total_value:,.2f}")
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
        st.subheader(f"What to buy with {plan.amount:,.2f} {base}")
        if plan.orders:
            st.dataframe(pd.DataFrame([
                {"Ticker": o.ticker, "Exchange": o.exchange, "Name": o.name, "Units": o.units,
                 "Price": float(o.price), "Ccy": o.currency, f"Brokerage ({base})":
                 float(o.brokerage), f"Total ({base})": float(o.total_cost)}
                for o in plan.orders
            ]), hide_index=True, column_config={"Price": st.column_config.NumberColumn(
                format="%.2f")})
            st.caption(f"Left over: {plan.leftover:,.2f} {base}. Prices are the latest "
                       "available and will move before you trade.")
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
# Colours from the dataviz reference palette, light and dark steps.

PALETTE = {
    "light": {"series": ["#2a78d6", "#eb6834"], "muted": "#898781", "grid": "#e1e0d9"},
    "dark": {"series": ["#3987e5", "#d95926"], "muted": "#898781", "grid": "#2c2c2a"},
}
SEVERITY_BADGE = {
    Severity.ALERT: "⛔ Alert", Severity.WARN: "⚠️ Warning",
    Severity.INFO: "ℹ️ Info", Severity.OK: "✅ OK",
}


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
    y = alt.Y("Label:N", sort=order, title=None, axis=alt.Axis(labelLimit=220, ticks=False))
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
    if not holdings:
        st.info("No holdings yet. Add them on the Holdings page, or import a broker export on "
                "the Import page.")
        return
    categories = repo.list_categories()
    profile = repo.get_profile()
    base = profile.base_currency if profile else "AUD"
    values = valued_holdings(holdings, base, _quotes, _fx_rates)
    value, cost = total(values)
    gain = value - cost

    m1, m2, m3, m4 = st.columns(4)
    m1.metric(f"Portfolio value ({base})", f"{value:,.0f}")
    m2.metric(f"Money invested ({base})", f"{cost:,.0f}")
    m3.metric(f"Gain ({base})", f"{gain:,.0f}",
              delta=f"{gain / cost * 100:.1f}%" if cost else None)
    m4.metric("Holdings", len(holdings))
    if any(not v.priced for v in values):
        st.caption("Some holdings have no current price and are counted at what you paid.")

    st.subheader("Value over time")
    period = st.radio("Period", list(PERIODS), index=2, horizontal=True, key="dash_period",
                      label_visibility="collapsed")
    today = date.today()
    first_buy = min(h.buy_date for h in holdings)
    days = PERIODS[period]
    start = max(first_buy, today - timedelta(days=days)) if days else first_buy
    points = portfolio_history(holdings, base, start, today, _history)
    if len(points) > 1:
        st.altair_chart(_value_chart(points, base), use_container_width=True)
        with st.expander("Show as a table"):
            st.dataframe(pd.DataFrame([{"Date": p.day, f"Value ({base})": float(p.value),
                                        f"Money invested ({base})": float(p.invested)}
                                       for p in points]), hide_index=True)
        st.caption("Money invested is what you paid, including fees, at the exchange rate on "
                   "the day. Value uses each day's closing price.")
    else:
        st.caption("Not enough history yet to draw a chart.")

    st.subheader("Where your money is")
    by = st.radio("Break down by", list(Dimension), horizontal=True, key="dash_by",
                  format_func=lambda d: d.value)
    slices = breakdown(values, categories, by)
    st.altair_chart(_bar_chart(slices, base), use_container_width=True)
    with st.expander("Show as a table"):
        st.dataframe(pd.DataFrame([{by.value: s.label, f"Value ({base})": float(s.value),
                                    "Share (%)": float(s.pct)} for s in slices]),
                     hide_index=True)

    st.subheader("Progress towards your goals")
    for cat in categories:
        in_goal = [v for v in values if v.holding.category_id == cat.id]
        p = goal_progress(cat, in_goal, today)
        with st.container(border=True):
            st.markdown(f"**{cat.name}**")
            if p.target:
                st.progress(min(float(p.value / p.target), 1.0),
                            text=f"{p.value:,.0f} of {p.target:,.0f} {base} "
                                 f"({p.pct_of_target:g}%)")
            else:
                st.markdown(f"{p.value:,.0f} {base}. Set a target amount on the Categories page "
                            "to track progress.")
            if p.on_track is True:
                st.caption(f"✅ On track: about {p.projected:,.0f} {base} by "
                           f"{cat.target_date:%b %Y} at an assumed "
                           f"{p.assumed_return * 100:g}% a year.")
            elif p.on_track is False:
                st.caption(f"⚠️ Behind: about {p.projected:,.0f} {base} by "
                           f"{cat.target_date:%b %Y} at an assumed {p.assumed_return * 100:g}% "
                           f"a year. Adding about {p.monthly_needed:,.0f} {base} a month would "
                           "close the gap.")
    st.caption("Projections assume steady returns of 7% a year for shares and 3.5% for cash, "
               "before fees and tax. Real returns vary a lot from year to year; "
               "this is an illustration, not a forecast.")


# Health check -----------------------------------------------------------------

def _health_page(repo: PortfolioRepository) -> None:
    holdings = repo.list_holdings()
    if not holdings:
        st.info("No holdings yet, so there's nothing to check.")
        return
    categories = repo.list_categories()
    profile = repo.get_profile()
    base = profile.base_currency if profile else "AUD"
    goal_prefs = {c.id: repo.get_category_preferences(c.id) for c in categories}
    report = health_report(categories, holdings, repo.get_preferences(), goal_prefs, base,
                           _quotes, _fx_rates, _fundamentals)

    st.caption("Checks each goal against its target mix and deadline, and the whole portfolio "
               "for concentration, fees and missing data. Fixes are always about where new "
               "money goes: nothing here suggests selling.")
    cols = st.columns(4)
    for col, sev in zip(cols, SEVERITY_ORDER, strict=True):
        col.metric(SEVERITY_BADGE[sev], report.count(sev))

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
    st.dataframe(pd.DataFrame([
        {"Platform": p.platform, f"Value ({base})": float(p.value), "Share (%)": float(p.pct),
         "Holdings": p.holdings, f"Invested ({base})": float(p.invested),
         f"Brokerage paid ({base})": float(p.fees), "Brokerage (% of invested)": float(p.fees_pct)}
        for p in report.platforms
    ]), hide_index=True)


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
    c1, c2, c3 = st.columns(3)
    platform = _platform_input(c1, platforms, broker.value if broker is not Broker.OTHER
                               else default_platform, f"import_platform_{run}")
    goal_names = [c.name for c in categories] + [UNCATEGORISED]
    goal = c2.selectbox("Goal for these holdings", goal_names, key=f"import_goal_{run}",
                        help="You can change it row by row below.")
    exchanges = list(EXCHANGES)
    default_exchange = c3.selectbox(
        "Exchange, when the file doesn't say", exchanges, key=f"import_exchange_{run}",
        index=exchanges.index(get_settings().default_exchange.upper())
        if get_settings().default_exchange.upper() in exchanges else 0)

    with st.expander(f"Columns ({LAYOUT_NAMES[mapping.layout]})",
                     expanded=bool(mapping.missing())):
        layouts = list(Layout)
        layout = st.radio("Layout", layouts, index=layouts.index(mapping.layout),
                          key=f"import_layout_{run}", format_func=LAYOUT_NAMES.get)
        options = [NONE] + header
        cols = st.columns(4)
        chosen = {}
        for i, f in enumerate(LAYOUT_FIELDS[layout]):
            current = mapping.columns.get(f)
            pick = cols[i % 4].selectbox(FIELD_NAMES.get(f, f.capitalize()), options,
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
        if st.form_submit_button("Save", type="primary"):
            try:
                repo.update_profile(ProfileIn(display_name=name, base_currency=ccy,
                                              default_platform=platform))
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

        page = st.radio("Go to", list(PAGES), key="page")

    st.title(page)
    try:
        if page == "Profile":
            _profile_page(repo, user.email)
        else:
            PAGES[page](repo)
    except APIError as exc:
        if exc.code != "42703":  # undefined column
            raise
        st.error("The database needs the Week 2 update. In Supabase, open SQL Editor and run "
                 "supabase/migrations/20261025000000_preferences.sql, then reload.")


PAGES = {
    "Dashboard": _dashboard_page,
    "Holdings": _holdings_page,
    "Import": _import_page,
    "Categories": _categories_page,
    "Preferences": _preferences_page,
    "Recommendations": _recommendations_page,
    "Health check": _health_page,
    "Profile": None,
}


main()
