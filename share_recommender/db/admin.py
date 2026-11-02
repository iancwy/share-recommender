"""Data access for the scheduled notification job, which uses the Supabase service key.

The service key bypasses row-level security, so unlike ``PortfolioRepository`` every query
in ``AdminRepository`` filters by ``user_id`` explicitly (tests check each method). Nothing
in the app or the API may use this module.
"""

from datetime import datetime
from uuid import UUID

from share_recommender.config import JobSettings
from share_recommender.core.models import CashAccount, Category, Holding, Profile
from share_recommender.core.notifications import Message
from share_recommender.core.preferences import CategoryPreferences, InvestorPreferences
from supabase import Client, ClientOptions, create_client


class AdminNotConfigured(RuntimeError):
    pass


def admin_client(settings: JobSettings) -> Client:
    if not (settings.supabase_url and settings.supabase_service_role_key):
        raise AdminNotConfigured("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY for the job. "
                                 "See docs/notifications-setup.md.")
    return create_client(settings.supabase_url, settings.supabase_service_role_key,
                         options=ClientOptions(persist_session=False, auto_refresh_token=False))


def list_people(client: Client) -> list[tuple[UUID, str | None]]:
    """Every person who has signed up, with their email."""
    people, page = [], 1
    while True:
        users = client.auth.admin.list_users(page=page, per_page=100)
        people += [(UUID(str(u.id)), u.email) for u in users]
        if len(users) < 100:
            return people
        page += 1


class AdminRepository:
    """Read one person's data and write their snapshots and notifications."""

    def __init__(self, client: Client, user_id: UUID):
        self._db = client
        self.user_id = user_id
        self._uid = str(user_id)

    def _profile_row(self) -> dict:
        rows = self._db.table("profiles").select("*").eq("id", self._uid).execute().data
        return rows[0] if rows else {}

    def profile(self) -> Profile | None:
        row = self._profile_row()
        return Profile.model_validate(row) if row else None

    def preferences(self) -> InvestorPreferences | None:
        prefs = self._profile_row().get("preferences")
        return InvestorPreferences.model_validate(prefs) if prefs else None

    def categories(self) -> tuple[list[Category], dict[UUID, CategoryPreferences | None]]:
        rows = (self._db.table("categories").select("*").eq("user_id", self._uid)
                .order("name").execute().data)
        return ([Category.model_validate(r) for r in rows],
                {UUID(r["id"]): CategoryPreferences.model_validate(r["preferences"])
                 if r.get("preferences") else None for r in rows})

    def holdings(self) -> list[Holding]:
        rows = self._db.table("holdings").select("*").eq("user_id", self._uid).execute().data
        return [Holding.model_validate(r) for r in rows]

    def cash_accounts(self) -> list[CashAccount]:
        rows = (self._db.table("cash_accounts").select("*").eq("user_id", self._uid)
                .execute().data)
        return [CashAccount.model_validate(r) for r in rows]

    def snapshots(self) -> dict[str, dict]:
        rows = (self._db.table("recommendation_snapshots").select("scope, payload")
                .eq("user_id", self._uid).execute().data)
        return {r["scope"]: r["payload"] for r in rows}

    def save_snapshots(self, snapshots: dict[str, dict]) -> None:
        now = datetime.now().astimezone().isoformat()
        if snapshots:
            self._db.table("recommendation_snapshots").upsert([
                {"user_id": self._uid, "scope": scope, "payload": payload, "created_at": now}
                for scope, payload in snapshots.items()]).execute()

    def add_notifications(self, messages: list[Message]) -> list[str]:
        if not messages:
            return []
        rows = self._db.table("notifications").insert([
            {"user_id": self._uid, "category_id": str(m.category_id) if m.category_id else None,
             "kind": m.kind, "title": m.title, "body": m.body, "payload": m.payload}
            for m in messages]).execute().data
        return [r["id"] for r in rows]

    def mark_emailed(self, notification_ids: list[str]) -> None:
        if notification_ids:
            (self._db.table("notifications")
             .update({"emailed_at": datetime.now().astimezone().isoformat()})
             .eq("user_id", self._uid).in_("id", notification_ids).execute())
