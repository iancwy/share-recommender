"""Read and write categories and holdings for the signed-in user.

The repository never filters by user id itself: row-level security in Postgres
does that, so a bug here cannot leak one person's portfolio to another.
"""

from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from postgrest.exceptions import APIError

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
from share_recommender.core.picker import PickRow, PickRun, pick_from_record, pick_record
from share_recommender.core.preferences import CategoryPreferences, InvestorPreferences
from supabase import Client


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def sydney_midnight() -> datetime:
    """The start of today in Sydney, as an aware datetime."""
    try:
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo("Australia/Sydney"))
    except Exception:  # no time zone database (e.g. Windows without tzdata): close enough
        now = datetime.now(UTC) + timedelta(hours=10)
        now = now.replace(tzinfo=timezone(timedelta(hours=10)))
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _payload(model: CategoryIn | HoldingIn | CashAccountIn | DividendIn | WatchlistItemIn
                   | ContributionIn) -> dict[str, Any]:
    return model.model_dump(mode="json")


class PortfolioRepository:
    def __init__(self, client: Client):
        self._db = client

    # Profile -------------------------------------------------------------

    def get_profile(self) -> Profile | None:
        rows = self._db.table("profiles").select("*").limit(1).execute().data
        return Profile.model_validate(rows[0]) if rows else None

    def update_profile(self, update: ProfileIn) -> Profile:
        profile = self.get_profile()
        if profile is None:
            raise LookupError("Profile not found")
        rows = (
            self._db.table("profiles")
            .update(update.model_dump(mode="json"))
            .eq("id", str(profile.id))
            .execute()
            .data
        )
        return Profile.model_validate(rows[0])

    def rename_platform(self, old: str, new: str | None) -> int:
        """Move every holding and cash account on platform ``old`` to ``new``.

        Cash accounts need a name, so they keep ``old`` if ``new`` is blank.
        Returns how many moved.
        """
        new = (new or "").strip() or None
        rows = (self._db.table("holdings").update({"platform": new}).eq("platform", old)
                .execute().data)
        if new:
            rows += (self._db.table("cash_accounts").update({"institution": new})
                     .eq("institution", old).execute().data)
        return len(rows)

    # Notifications (created by the scheduled job; people can only read, mark read, delete)

    def list_notifications(self, limit: int = 50) -> list[dict]:
        return (self._db.table("notifications").select("*").order("created_at", desc=True)
                .limit(limit).execute().data)

    def unread_count(self) -> int:
        return len(self._db.table("notifications").select("id").is_("read_at", "null")
                   .execute().data)

    def mark_notification_read(self, notification_id: str) -> None:
        (self._db.table("notifications").update({"read_at": _now()})
         .eq("id", notification_id).execute())

    def mark_all_notifications_read(self) -> None:
        (self._db.table("notifications").update({"read_at": _now()})
         .is_("read_at", "null").execute())

    def delete_notification(self, notification_id: str) -> None:
        self._db.table("notifications").delete().eq("id", notification_id).execute()

    def get_snapshots(self) -> dict[str, dict]:
        """The job's last recommendation snapshots, for the 'check for changes' preview."""
        rows = self._db.table("recommendation_snapshots").select("scope, payload").execute().data
        return {r["scope"]: r["payload"] for r in rows}

    # Interview answers ---------------------------------------------------
    # An empty JSON object means the interview hasn't been done, returned as None.

    def get_preferences(self) -> InvestorPreferences | None:
        rows = self._db.table("profiles").select("preferences").limit(1).execute().data
        return InvestorPreferences.model_validate(rows[0]["preferences"]) if (
            rows and rows[0]["preferences"]) else None

    def save_preferences(self, prefs: InvestorPreferences) -> InvestorPreferences:
        profile = self.get_profile()
        if profile is None:
            raise LookupError("Profile not found")
        rows = (
            self._db.table("profiles")
            .update({"preferences": prefs.model_dump(mode="json")})
            .eq("id", str(profile.id))
            .execute()
            .data
        )
        return InvestorPreferences.model_validate(rows[0]["preferences"])

    def get_category_preferences(self, category_id: UUID) -> CategoryPreferences | None:
        rows = (self._db.table("categories").select("preferences")
                .eq("id", str(category_id)).execute().data)
        if not rows:
            raise LookupError(f"Category {category_id} not found")
        return CategoryPreferences.model_validate(rows[0]["preferences"]) if (
            rows[0]["preferences"]) else None

    def save_category_preferences(self, category_id: UUID, prefs: CategoryPreferences
                                  ) -> CategoryPreferences:
        rows = (
            self._db.table("categories")
            .update({"preferences": prefs.model_dump(mode="json")})
            .eq("id", str(category_id))
            .execute()
            .data
        )
        if not rows:
            raise LookupError(f"Category {category_id} not found")
        return CategoryPreferences.model_validate(rows[0]["preferences"])

    # Categories ----------------------------------------------------------

    def list_categories(self) -> list[Category]:
        rows = self._db.table("categories").select("*").order("name").execute().data
        return [Category.model_validate(r) for r in rows]

    def create_category(self, category: CategoryIn) -> Category:
        rows = self._db.table("categories").insert(_payload(category)).execute().data
        return Category.model_validate(rows[0])

    def update_category(self, category_id: UUID, category: CategoryIn) -> Category:
        rows = (
            self._db.table("categories")
            .update(_payload(category))
            .eq("id", str(category_id))
            .execute()
            .data
        )
        if not rows:
            raise LookupError(f"Category {category_id} not found")
        return Category.model_validate(rows[0])

    def delete_category(self, category_id: UUID) -> None:
        """Holdings in the category are kept and become uncategorised."""
        self._db.table("categories").delete().eq("id", str(category_id)).execute()

    # Holdings ------------------------------------------------------------

    def list_holdings(self, category_id: UUID | None = None) -> list[Holding]:
        query = self._db.table("holdings").select("*")
        if category_id is not None:
            query = query.eq("category_id", str(category_id))
        rows = query.order("buy_date", desc=True).execute().data
        return [Holding.model_validate(r) for r in rows]

    def get_holding(self, holding_id: UUID) -> Holding | None:
        rows = self._db.table("holdings").select("*").eq("id", str(holding_id)).execute().data
        return Holding.model_validate(rows[0]) if rows else None

    def create_holding(self, holding: HoldingIn) -> Holding:
        rows = self._db.table("holdings").insert(_payload(holding)).execute().data
        return Holding.model_validate(rows[0])

    def update_holding(self, holding_id: UUID, holding: HoldingIn) -> Holding:
        rows = (
            self._db.table("holdings")
            .update(_payload(holding))
            .eq("id", str(holding_id))
            .execute()
            .data
        )
        if not rows:
            raise LookupError(f"Holding {holding_id} not found")
        return Holding.model_validate(rows[0])

    def delete_holding(self, holding_id: UUID) -> None:
        self._db.table("holdings").delete().eq("id", str(holding_id)).execute()

    # Cash accounts -------------------------------------------------------

    def list_cash_accounts(self, category_id: UUID | None = None) -> list[CashAccount]:
        query = self._db.table("cash_accounts").select("*")
        if category_id is not None:
            query = query.eq("category_id", str(category_id))
        rows = query.order("institution").execute().data
        return [CashAccount.model_validate(r) for r in rows]

    def create_cash_account(self, account: CashAccountIn) -> CashAccount:
        rows = self._db.table("cash_accounts").insert(_payload(account)).execute().data
        return CashAccount.model_validate(rows[0])

    def update_cash_account(self, account_id: UUID, account: CashAccountIn) -> CashAccount:
        rows = (
            self._db.table("cash_accounts")
            .update(_payload(account))
            .eq("id", str(account_id))
            .execute()
            .data
        )
        if not rows:
            raise LookupError(f"Cash account {account_id} not found")
        return CashAccount.model_validate(rows[0])

    def delete_cash_account(self, account_id: UUID) -> None:
        self._db.table("cash_accounts").delete().eq("id", str(account_id)).execute()

    # Dividends -----------------------------------------------------------

    def list_dividends(self) -> list[Dividend]:
        rows = self._db.table("dividends").select("*").order("pay_date", desc=True).execute().data
        return [Dividend.model_validate(r) for r in rows]

    def create_dividend(self, dividend: DividendIn) -> Dividend:
        rows = self._db.table("dividends").insert(_payload(dividend)).execute().data
        return Dividend.model_validate(rows[0])

    def delete_dividend(self, dividend_id: UUID) -> None:
        self._db.table("dividends").delete().eq("id", str(dividend_id)).execute()

    # Watchlist -----------------------------------------------------------

    def list_watchlist(self) -> list[WatchlistItem]:
        rows = self._db.table("watchlist").select("*").order("ticker").execute().data
        return [WatchlistItem.model_validate(r) for r in rows]

    def get_watchlist_item(self, item_id: UUID) -> WatchlistItem | None:
        rows = self._db.table("watchlist").select("*").eq("id", str(item_id)).execute().data
        return WatchlistItem.model_validate(rows[0]) if rows else None

    def add_to_watchlist(self, item: WatchlistItemIn) -> WatchlistItem:
        try:
            rows = self._db.table("watchlist").insert(_payload(item)).execute().data
        except APIError as exc:
            if exc.code == "23505":  # unique violation: already there
                raise ValueError("Already on your watchlist") from exc
            raise
        return WatchlistItem.model_validate(rows[0])

    def update_watchlist_item(self, item_id: UUID, item: WatchlistItemIn) -> WatchlistItem:
        rows = (self._db.table("watchlist").update(_payload(item)).eq("id", str(item_id))
                .execute().data)
        if not rows:
            raise LookupError(f"Watchlist item {item_id} not found")
        return WatchlistItem.model_validate(rows[0])

    def remove_from_watchlist(self, item_id: UUID) -> None:
        self._db.table("watchlist").delete().eq("id", str(item_id)).execute()

    # AI picker runs and picks (a record: written once, never edited) ------

    def latest_pick_run(self, include_failed: bool = False) -> PickRun | None:
        query = self._db.table("pick_runs").select("*")
        if not include_failed:
            query = query.eq("status", "ok")
        rows = query.order("created_at", desc=True).limit(1).execute().data
        return PickRun.model_validate(rows[0]) if rows else None

    def list_picks(self, run_id: UUID | str) -> list[PickRow]:
        rows = (self._db.table("ai_picks").select("*").eq("run_id", str(run_id))
                .order("rank").execute().data)
        return [pick_from_record(r) for r in rows]

    def save_pick_run(self, run: PickRun, picks: list[PickRow]) -> str:
        run_id = self._db.table("pick_runs").insert(run.record()).execute().data[0]["id"]
        if picks:
            self._db.table("ai_picks").insert([pick_record(p, run_id) for p in picks]).execute()
        return run_id

    def pick_runs_today(self) -> int:
        """Manual runs since midnight in Sydney (for the daily limit)."""
        rows = (self._db.table("pick_runs").select("id").eq("trigger", "manual")
                .gte("created_at", sydney_midnight().isoformat()).execute().data)
        return len(rows)

    def prune_pick_runs(self, keep: int = 10) -> None:
        """Delete all but the newest ``keep`` runs; their picks go with them."""
        rows = (self._db.table("pick_runs").select("id").order("created_at", desc=True)
                .execute().data)
        old = [r["id"] for r in rows[keep:]]
        if old:
            self._db.table("pick_runs").delete().in_("id", old).execute()

    # Regular contributions -------------------------------------------------

    def list_contributions(self) -> list[Contribution]:
        rows = (self._db.table("contributions").select("*").order("start_date").execute()
                .data)
        return [Contribution.model_validate(r) for r in rows]

    def add_contribution(self, c: ContributionIn) -> Contribution:
        rows = self._db.table("contributions").insert(_payload(c)).execute().data
        return Contribution.model_validate(rows[0])

    def update_contribution(self, contribution_id: UUID, c: ContributionIn) -> Contribution:
        rows = (self._db.table("contributions").update(_payload(c))
                .eq("id", str(contribution_id)).execute().data)
        if not rows:
            raise LookupError(f"Contribution {contribution_id} not found")
        return Contribution.model_validate(rows[0])

    def delete_contribution(self, contribution_id: UUID) -> None:
        self._db.table("contributions").delete().eq("id", str(contribution_id)).execute()
