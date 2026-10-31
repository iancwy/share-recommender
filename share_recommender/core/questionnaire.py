"""The preferences questionnaire: every question, its allowed answers and the field it fills.

This is the single definition of what gets asked and saved. The Streamlit form renders it
directly, and the chat interview (``ai/interview.py``) is given the same questions so it can
fill in the same fields from a conversation. Answers are saved as ``InvestorPreferences``
(about the person) and ``CategoryPreferences`` (per goal).
"""

from dataclasses import dataclass
from typing import Literal

from share_recommender.core.exchanges import EXCHANGES
from share_recommender.core.preferences import (
    DirectShares,
    DrawdownReaction,
    Experience,
    Flexibility,
    GoalStyle,
    HomeBias,
    IncomeGrowth,
)
from share_recommender.core.universe import EXCLUSION_LABELS, THEME_LABELS

Kind = Literal["choice", "multi", "number", "tickers", "text"]


@dataclass(frozen=True)
class Question:
    field: str          # attribute on InvestorPreferences or CategoryPreferences
    text: str
    kind: Kind
    options: dict | None = None  # answer value -> label, for "choice" and "multi"
    help: str | None = None
    min: float | None = None
    max: float | None = None
    step: float | None = None


ABOUT_YOU: list[Question] = [
    Question("experience", "How much investing have you done?", "choice", {
        Experience.BEGINNER: "New to it: I've bought little or nothing myself",
        Experience.INTERMEDIATE: "Some experience: I've bought shares or ETFs a few times",
        Experience.EXPERIENCED: "Experienced: I invest regularly and follow the market",
    }),
    Question("drawdown_reaction", "Imagine your investments fell 20% over a few months. What "
             "would you most likely do?", "choice", {
                 DrawdownReaction.SELL_ALL: "Sell everything to stop further losses",
                 DrawdownReaction.SELL_SOME: "Sell some to reduce the risk",
                 DrawdownReaction.HOLD: "Hold and wait for it to recover",
                 DrawdownReaction.BUY_MORE: "Buy more while prices are low",
             }, help="There's no right answer. Falls like this happen every few years."),
    Question("markets", "Which stock exchanges are you happy to buy on?", "multi",
             {code: f"{code}: {e.name} ({e.currency})" for code, e in EXCHANGES.items()},
             help="Your broker needs to offer them. Overseas shares are bought in that "
                  "market's currency."),
    Question("home_bias", "How much of your shares would you like in Australian companies?",
             "choice", {
                 HomeBias.MOSTLY_AUSTRALIAN: "Mostly Australian (about half)",
                 HomeBias.BALANCED: "A balance (about a third Australian)",
                 HomeBias.MOSTLY_INTERNATIONAL: "Mostly international (about a fifth Australian)",
             }),
    Question("direct_shares", "ETFs or individual company shares?", "choice", {
        DirectShares.ETF_ONLY: "ETFs only: simplest and most diversified",
        DirectShares.SOME: "Mostly ETFs, with some individual shares (about a quarter)",
        DirectShares.MIX: "A real mix (about half in individual shares)",
    }, help="An ETF holds hundreds of companies in one trade. Individual shares are more "
            "hands-on and less diversified."),
    Question("income_growth", "What do you want most from your shares?", "choice", {
        IncomeGrowth.INCOME: "Regular income from dividends",
        IncomeGrowth.BALANCED: "A bit of both",
        IncomeGrowth.GROWTH: "Long-term growth in value",
    }),
    Question("exclusions", "Any industries you'd rather not invest in?", "multi",
             dict(EXCLUSION_LABELS)),
    Question("avoid_tickers", "Any particular companies or funds to leave out?", "tickers",
             help="Tickers separated by commas, e.g. CBA, BHP. Useful if you already have a "
                  "lot of one company, such as employee shares."),
    Question("brokerage_per_trade", "Typical brokerage per trade, in your base currency",
             "number", min=0, step=1,
             help="Used to avoid suggesting trades too small to be worth the fee."),
    Question("notes", "Anything else that should shape your investing?", "text",
             help="For example plans, constraints or worries. Shown with your "
                  "recommendations and used in the plain-English explanation."),
]

GOAL: list[Question] = [
    Question("horizon_years", "When do you need this money? (years from now)", "number",
             min=0, max=60, step=1,
             help="For a goal with no end date, such as general investing, use 10 or more. "
                  "Not asked when the category has a target date."),
    Question("flexibility", "If this goal came up short, how much would it matter?", "choice", {
        Flexibility.MUST_MEET: "It must be there in full when I need it",
        Flexibility.FLEXIBLE: "I could wait a little longer or accept a bit less",
        Flexibility.NICE_TO_HAVE: "It's a nice-to-have; falling short is fine",
    }),
    Question("style", "What kind of goal is this?", "choice", {
        GoalStyle.CORE: "An ordinary goal: a sensible mix of shares and cash",
        GoalStyle.SPECULATIVE: "Speculative: a small pot for high-growth, high-risk companies",
    }, help="Speculative goals are all in shares of fast-growing companies. Keep them to money "
            "you could afford to lose."),
    Question("themes", "For a speculative goal: which themes?", "multi", dict(THEME_LABELS),
             help="Ignored for ordinary goals."),
    Question("notes", "Anything else about this goal?", "text",
             help="For example who it's for, or how the money will be used."),
]


def describe(questions: list[Question]) -> str:
    """The questions as plain text, with each field name and its allowed answers."""
    lines = []
    for q in questions:
        line = f"- {q.field} ({q.kind}): {q.text}"
        if q.options:
            line += " Allowed values: " + "; ".join(f"{v} = {label}"
                                                     for v, label in q.options.items())
        if q.min is not None or q.max is not None:
            line += f" Range: {q.min if q.min is not None else ''} to " \
                    f"{q.max if q.max is not None else 'no limit'}."
        if q.help:
            line += f" Note: {q.help}"
        lines.append(line)
    return "\n".join(lines)
