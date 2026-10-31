"""Read and write categories and holdings for the signed-in user.

The repository never filters by user id itself: row-level security in Postgres
does that, so a bug here cannot leak one person's portfolio to another.
"""

from typing import Any
from uuid import UUID

from share_recommender.core.models import (
    Category,
    CategoryIn,
    Holding,
    HoldingIn,
    Profile,
    ProfileIn,
)
from share_recommender.core.preferences import CategoryPreferences, InvestorPreferences
from supabase import Client


def _payload(model: CategoryIn | HoldingIn) -> dict[str, Any]:
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
        """Move every holding on platform ``old`` to ``new``. Returns how many moved."""
        new = (new or "").strip() or None
        rows = (self._db.table("holdings").update({"platform": new}).eq("platform", old)
                .execute().data)
        return len(rows)

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
