"""Domain models shared by the API, the UI and (later) the recommender."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from share_recommender.core.exchanges import EXCHANGES


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CategoryIn(BaseModel):
    """Fields a user can set on a goal category."""

    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    target_amount: Decimal | None = Field(default=None, ge=0)
    target_date: date | None = None
    risk_level: RiskLevel = RiskLevel.MEDIUM

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name cannot be blank")
        return v


class Category(CategoryIn):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    created_at: datetime | None = None
    updated_at: datetime | None = None


class HoldingIn(BaseModel):
    """One purchase (parcel) of a share or ETF, entered manually by the user.

    Buying the same ticker twice gives two rows, which keeps cost base and
    dates accurate for later performance and tax calculations.
    """

    ticker: str = Field(min_length=1, max_length=20)
    exchange: str
    units: Decimal = Field(gt=0)
    buy_price: Decimal = Field(gt=0, description="Price per unit, in the exchange currency")
    buy_date: date
    fees: Decimal = Field(default=Decimal("0"), ge=0)
    platform: str | None = Field(default=None, max_length=50, description="e.g. CMC Markets")
    category_id: UUID | None = None
    notes: str | None = None

    @field_validator("ticker")
    @classmethod
    def _normalise_ticker(cls, v: str) -> str:
        v = v.strip().upper()
        if not v:
            raise ValueError("Ticker cannot be blank")
        return v

    @field_validator("exchange")
    @classmethod
    def _known_exchange(cls, v: str) -> str:
        v = v.strip().upper()
        if v not in EXCHANGES:
            raise ValueError(f"Unknown exchange '{v}'. Known: {', '.join(EXCHANGES)}")
        return v

    @field_validator("buy_date")
    @classmethod
    def _not_in_future(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("Buy date cannot be in the future")
        return v

    @field_validator("platform")
    @classmethod
    def _blank_platform_is_none(cls, v: str | None) -> str | None:
        return v.strip() or None if v is not None else None

    @property
    def cost_base(self) -> Decimal:
        """Total amount paid including fees."""
        return self.units * self.buy_price + self.fees

    @property
    def currency(self) -> str:
        return EXCHANGES[self.exchange].currency


class Holding(HoldingIn):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("buy_date")
    @classmethod
    def _not_in_future(cls, v: date) -> date:
        # Stored rows are trusted; only new input is checked against today.
        return v


class Profile(BaseModel):
    id: UUID
    display_name: str | None = None
    base_currency: str = "AUD"
