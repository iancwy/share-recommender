"""Notification job: tell each person what's new in their recommendations.

Run by GitHub Actions after the ASX closes on weekdays (.github/workflows/notify.yml), or:

    python -m share_recommender.jobs.notify --dry-run            # print, save and send nothing
    python -m share_recommender.jobs.notify --user ian@example.com --force

People choose Off, Daily (weekdays) or Weekly (Mondays, Sydney time) on their Profile page.
--force runs weekly people on any day; nobody set to Off is ever processed.
Needs SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY, and to email, the SMTP_* settings and
EMAIL_FROM. See docs/notifications-setup.md.
"""

import argparse
import logging
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from share_recommender.config import JobSettings
from share_recommender.core.notifications import compose_email
from share_recommender.data import MarketDataProvider, get_provider
from share_recommender.data.history import CachedHistory
from share_recommender.db.admin import AdminRepository, admin_client, list_people
from share_recommender.db.price_store import SupabasePriceStore
from share_recommender.mailer import EmailSender, PrintSender, SmtpSender
from share_recommender.notifier import build_notifications
from share_recommender.recommendations import provider_fns

log = logging.getLogger("notify")


def sydney_today() -> date:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Australia/Sydney")).date()
    except Exception:  # no time zone database (e.g. Windows without tzdata): close enough
        return (datetime.now(UTC) + timedelta(hours=10)).date()


def is_due(frequency: str, today: date, force: bool) -> bool:
    if frequency == "off":
        return False
    if force:
        return True
    if frequency == "weekly":
        return today.weekday() == 0
    return today.weekday() < 5  # daily means weekdays


def run(people: list[tuple[UUID, str | None]], repo_for: Callable[[UUID], AdminRepository],
        provider: MarketDataProvider, sender: EmailSender, *, today: date, app_url: str,
        dry_run: bool = False, only: str | None = None, force: bool = False,
        price_store=None) -> int:
    """Process everyone due. Returns how many people it failed for."""
    failures = 0
    quotes_fn, fx_fn, fundamentals_fn = provider_fns(provider)
    for user_id, email in people:
        if only and (email or "").lower() != only.lower():
            continue
        try:
            repo = repo_for(user_id)
            profile = repo.profile()
            if profile is None:
                continue
            if not is_due(profile.notify_frequency, today, force):
                log.info("%s: not due (%s)", email, profile.notify_frequency)
                continue
            categories, goal_prefs = repo.categories()
            history = CachedHistory(provider, price_store, today=today)

            def fetch(symbols, currencies, base, start, end, history=history):
                return ({s: history.history(*s, start, end) for s in symbols},
                        {c: history.fx_history(base, c, start, end) for c in currencies})

            outcome = build_notifications(
                categories=categories, goal_prefs=goal_prefs, holdings=repo.holdings(),
                cash_accounts=repo.cash_accounts(), inv=repo.preferences(),
                base=profile.base_currency, old_snapshots=repo.snapshots(),
                quotes_fn=quotes_fn, fx_fn=fx_fn, fundamentals_fn=fundamentals_fn,
                history_fn=fetch, today=today, weekly=profile.notify_frequency == "weekly")

            if outcome.baseline:
                log.info("%s: first run, baseline stored", email)
            else:
                log.info("%s: %d notification(s)", email, len(outcome.messages))
            if dry_run:
                for m in outcome.messages:
                    print(f"[{email}] {m.goal or 'Portfolio'}: {m.title}\n  {m.body}", flush=True)
                if outcome.messages and email and profile.notify_email:
                    PrintSender().send(email, *compose_email(outcome.messages,
                                                             profile.display_name, app_url))
                continue

            ids = repo.add_notifications(outcome.messages)
            if outcome.messages and email and profile.notify_email:
                sender.send(email, *compose_email(outcome.messages, profile.display_name,
                                                  app_url))
                repo.mark_emailed(ids)
            repo.save_snapshots(outcome.snapshots)
        except Exception:
            failures += 1
            log.exception("%s: failed", email or user_id)
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would be sent and saved; write and send nothing")
    parser.add_argument("--user", metavar="EMAIL", help="only this person")
    parser.add_argument("--force", action="store_true",
                        help="ignore the weekly day (people set to Off are still skipped)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)

    settings = JobSettings()
    if args.dry_run:
        sender: EmailSender = PrintSender()
    elif settings.email_configured:
        sender = SmtpSender(settings.smtp_host, settings.smtp_port, settings.smtp_user,
                            settings.smtp_password, settings.email_from or settings.smtp_user)
    else:
        log.error("Set SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD and EMAIL_FROM to send "
                  "email, or use --dry-run.")
        return 2
    client = admin_client(settings)
    failures = run(list_people(client), lambda uid: AdminRepository(client, uid),
                   get_provider(settings.market_data_provider), sender, today=sydney_today(),
                   app_url=settings.app_url, dry_run=args.dry_run, only=args.user,
                   force=args.force, price_store=SupabasePriceStore(client))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
