"""Domain models shared by the API, the UI and (later) the recommender."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
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


class CashAccountIn(BaseModel):
    """Cash in a savings account, entered by hand and updated now and then."""

    institution: str = Field(min_length=1, max_length=50, description="Bank or platform")
    account_name: str | None = Field(default=None, max_length=100)
    balance: Decimal = Field(ge=0)
    interest_rate: Decimal = Field(default=Decimal("0"), ge=0, le=100,
                                   description="% a year, e.g. 5.25")
    currency: str = Field(default="AUD", pattern=r"^[A-Z]{3}$")
    as_of: date = Field(default_factory=date.today, description="When the balance was checked")
    category_id: UUID | None = None
    notes: str | None = None

    @field_validator("institution")
    @classmethod
    def _strip_institution(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Bank or platform cannot be blank")
        return v

    @field_validator("account_name", "notes")
    @classmethod
    def _blank_is_none(cls, v: str | None) -> str | None:
        return v.strip() or None if v is not None else None

    @field_validator("currency", mode="before")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.strip().upper() if isinstance(v, str) else v

    @field_validator("as_of")
    @classmethod
    def _not_in_future(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("Balance date cannot be in the future")
        return v

    @property
    def label(self) -> str:
        return f"{self.institution} {self.account_name}" if self.account_name else self.institution


class CashAccount(CashAccountIn):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("as_of")
    @classmethod
    def _not_in_future(cls, v: date) -> date:
        return v


class DividendIn(BaseModel):
    """One dividend or distribution payment, in the exchange's currency."""

    ticker: str = Field(min_length=1, max_length=20)
    exchange: str
    pay_date: date
    amount: Decimal = Field(gt=0, description="Cash paid (or reinvested), before any tax")
    franking_credits: Decimal = Field(default=Decimal("0"), ge=0)
    withholding_tax: Decimal = Field(default=Decimal("0"), ge=0,
                                     description="Foreign tax withheld, e.g. on US shares")
    reinvested: bool = Field(default=False, description="Taken as new units (DRP)")
    platform: str | None = Field(default=None, max_length=50)
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

    @field_validator("platform", "notes")
    @classmethod
    def _blank_is_none(cls, v: str | None) -> str | None:
        return v.strip() or None if v is not None else None

    @field_validator("pay_date")
    @classmethod
    def _not_in_future(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("Pay date cannot be in the future")
        return v

    @property
    def currency(self) -> str:
        return EXCHANGES[self.exchange].currency

    @property
    def grossed_up(self) -> Decimal:
        """Cash plus franking credits: the amount that counts as taxable income."""
        return self.amount + self.franking_credits


class Dividend(DividendIn):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("pay_date")
    @classmethod
    def _not_in_future(cls, v: date) -> date:
        return v


FALLBACK_PLATFORM = "CMC Markets"


class ProfileIn(BaseModel):
    """Fields a person can change on their profile."""

    display_name: str | None = Field(default=None, max_length=100)
    base_currency: str = Field(default="AUD", pattern=r"^[A-Z]{3}$")
    default_platform: str | None = Field(default=None, max_length=50,
                                         description="For new holdings and imports")
    notify_email: bool = Field(default=True, description="Email new notifications")
    notify_frequency: Literal["off", "daily", "weekly"] = Field(
        default="weekly", description="Daily means weekdays; weekly means Mondays")

    @field_validator("display_name", "default_platform")
    @classmethod
    def _blank_is_none(cls, v: str | None) -> str | None:
        return v.strip() or None if v is not None else None


class Profile(ProfileIn):
    id: UUID

    @property
    def platform_for_new(self) -> str:
        return self.default_platform or FALLBACK_PLATFORM
