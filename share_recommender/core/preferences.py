"""Answers from the guided preferences interview.

``InvestorPreferences`` is stored once per person (on their profile) and
``CategoryPreferences`` once per goal category. Both are saved as JSON so questions can
be added later without a migration; every field has a default so older answers still load.
"""

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from share_recommender.core.exchanges import EXCHANGES
from share_recommender.core.universe import Exclusion, Theme


class Experience(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    EXPERIENCED = "experienced"


class DrawdownReaction(StrEnum):
    """What the person would do if their investments fell 20% in a few months."""

    SELL_ALL = "sell_all"
    SELL_SOME = "sell_some"
    HOLD = "hold"
    BUY_MORE = "buy_more"


class HomeBias(StrEnum):
    MOSTLY_AUSTRALIAN = "mostly_australian"
    BALANCED = "balanced"
    MOSTLY_INTERNATIONAL = "mostly_international"


class DirectShares(StrEnum):
    """How much of the shares portion goes into individual companies rather than ETFs."""

    ETF_ONLY = "etf_only"
    SOME = "some"
    MIX = "mix"


class IncomeGrowth(StrEnum):
    INCOME = "income"
    BALANCED = "balanced"
    GROWTH = "growth"


class Flexibility(StrEnum):
    """How much it matters if the goal falls short."""

    MUST_MEET = "must_meet"
    FLEXIBLE = "flexible"
    NICE_TO_HAVE = "nice_to_have"


class GoalStyle(StrEnum):
    CORE = "core"                # a diversified mix of shares, bonds and cash
    SPECULATIVE = "speculative"  # a small pot all in high-growth themes


class InvestorPreferences(BaseModel):
    experience: Experience = Experience.INTERMEDIATE
    drawdown_reaction: DrawdownReaction = DrawdownReaction.HOLD
    markets: list[str] = Field(default_factory=lambda: ["ASX", "NYSE", "NASDAQ"],
                               min_length=1, description="Exchange codes the person will buy on")
    home_bias: HomeBias = HomeBias.BALANCED
    direct_shares: DirectShares = DirectShares.SOME
    income_growth: IncomeGrowth = IncomeGrowth.BALANCED
    exclusions: list[Exclusion] = Field(default_factory=list)
    avoid_tickers: list[str] = Field(default_factory=list,
                                     description="Tickers never to recommend, on any exchange")
    brokerage_per_trade: Decimal = Field(default=Decimal("0"), ge=0,
                                         description="Typical brokerage per trade, base currency")
    notes: str | None = Field(default=None, max_length=2000,
                              description="Anything the questions don't cover, in their words")

    @field_validator("markets")
    @classmethod
    def _known_markets(cls, v: list[str]) -> list[str]:
        out = []
        for code in v:
            code = code.strip().upper()
            if code not in EXCHANGES:
                raise ValueError(f"Unknown exchange '{code}'. Known: {', '.join(EXCHANGES)}")
            if code not in out:
                out.append(code)
        return out

    @field_validator("avoid_tickers")
    @classmethod
    def _normalise_tickers(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(t.strip().upper() for t in v if t.strip()))

    @field_validator("notes")
    @classmethod
    def _blank_notes_is_none(cls, v: str | None) -> str | None:
        return v.strip() or None if v is not None else None


class CategoryPreferences(BaseModel):
    horizon_years: int | None = Field(
        default=None, ge=0, le=60,
        description="Years until the money is needed. Ignored when the category has a target date.",
    )
    flexibility: Flexibility = Flexibility.FLEXIBLE
    style: GoalStyle | None = Field(
        default=None, description="Unset means core, except for a category named 'Speculative'.")
    themes: list[Theme] = Field(default_factory=lambda: [Theme.AI],
                                description="Themes for a speculative goal")
    notes: str | None = Field(default=None, max_length=2000,
                              description="Anything about this goal the questions don't cover")

    @field_validator("notes")
    @classmethod
    def _blank_notes_is_none(cls, v: str | None) -> str | None:
        return v.strip() or None if v is not None else None
