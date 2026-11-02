"""What's new in a person's recommendations, and the notifications about it. Pure functions.

For each goal (and once for the whole portfolio) the job stores a small **snapshot**: the
recommended picks and their weights, and the health findings at Warning or Alert level.
Comparing it with the last snapshot gives what's new:

- a pick added or removed, or a pick's weight changing by 5 points or more;
- a Warning or Alert finding that wasn't there last time.

Findings that clear are mentioned as good news, but never trigger a notification on their
own. The first run for a person only stores a baseline.

Two extras sit alongside: **big price moves** in a holding, and **goal deadline reminders**
(24, 12, 6, 3 and 1 month(s) before a target date). What they've already said is kept in the
portfolio snapshot's ``sent`` list, so each is sent once.
"""

import html
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID

from share_recommender.core.health import Finding, Severity
from share_recommender.core.models import Category, Holding
from share_recommender.core.performance import GoalProgress
from share_recommender.core.recommender import Recommendation
from share_recommender.data.base import PriceBar

PORTFOLIO = "portfolio"  # the snapshot scope for findings that aren't about one goal
WEIGHT_CHANGE = Decimal(5)
NOTIFY_LEVELS = {Severity.WARN, Severity.ALERT}
DEADLINE_MONTHS = [24, 12, 6, 3, 1]
PRICE_MOVE_PCT = {1: Decimal(5), 7: Decimal(10)}  # over a day for daily, a week for weekly
STALE_PRICE_DAYS = 4
MAX_SENT_KEYS = 300
DISCLAIMER = ("General information from a rules-based tool, not personal financial advice.")


# Snapshots and differences -----------------------------------------------------

def finding_key(f: Finding) -> str:
    """Stable while the issue lasts: numbers in the title change from run to run."""
    return f"{f.area}|{re.sub(r'[0-9.,%]+', '#', f.title)}"


def snapshot(rec: Recommendation | None, findings: list[Finding],
             sent: list[str] = ()) -> dict:
    """What a goal (or the portfolio) looks like now. Stored as JSON."""
    return {
        "picks": ({f"{p.ticker}:{p.exchange}": str(p.weight) for p in rec.mix.picks}
                  if rec else {}),
        "findings": {finding_key(f): {"severity": f.severity.value, "title": f.title}
                     for f in findings if f.severity in NOTIFY_LEVELS},
        "sent": list(sent)[-MAX_SENT_KEYS:],
    }


@dataclass
class Change:
    added: list[str] = field(default_factory=list)          # "VAS (ASX) at 22.5%"
    removed: list[str] = field(default_factory=list)
    reweighted: list[str] = field(default_factory=list)     # "VGS (ASX) 40% -> 31%"
    new_findings: list[dict] = field(default_factory=list)  # {"severity", "title"}
    cleared: list[str] = field(default_factory=list)        # titles, good news only

    @property
    def due(self) -> bool:
        """Clearing alone never triggers a notification."""
        return bool(self.added or self.removed or self.reweighted or self.new_findings)

    @property
    def picks_changed(self) -> bool:
        return bool(self.added or self.removed or self.reweighted)


def _label(key: str) -> str:
    ticker, exchange = key.split(":", 1)
    return f"{ticker} ({exchange})"


def diff(old: dict | None, new: dict) -> Change | None:
    """What changed since the last snapshot. None for the first run (baseline only)."""
    if old is None:
        return None
    change = Change()
    old_picks, new_picks = old.get("picks", {}), new.get("picks", {})
    for key, weight in new_picks.items():
        if key not in old_picks:
            change.added.append(f"{_label(key)} at {Decimal(weight):g}%")
        elif abs(Decimal(weight) - Decimal(old_picks[key])) >= WEIGHT_CHANGE:
            change.reweighted.append(f"{_label(key)} {Decimal(old_picks[key]):g}% → "
                                     f"{Decimal(weight):g}%")
    change.removed = [_label(k) for k in old_picks if k not in new_picks]
    old_f, new_f = old.get("findings", {}), new.get("findings", {})
    change.new_findings = [f for k, f in new_f.items() if k not in old_f]
    change.cleared = [f["title"] for k, f in old_f.items() if k not in new_f]
    return change


# Extras: price moves and deadline reminders --------------------------------------

def price_moves(holdings: list[Holding], prices: dict[tuple[str, str], list[PriceBar]],
                days: int, already_sent: set[str], today: date) -> list[tuple[str, str]]:
    """(key, line) for each holding that moved more than the threshold over ``days``."""
    threshold = PRICE_MOVE_PCT[days]
    held: dict[tuple[str, str], Decimal] = {}
    currency: dict[tuple[str, str], str] = {}
    for h in holdings:
        held[(h.ticker, h.exchange)] = held.get((h.ticker, h.exchange), Decimal(0)) + h.units
        currency[(h.ticker, h.exchange)] = h.currency
    out = []
    for key, units in sorted(held.items()):
        bars = sorted(prices.get(key, []), key=lambda b: b.day)
        if len(bars) < 2 or (today - bars[-1].day).days > STALE_PRICE_DAYS:
            continue
        last = bars[-1]
        earlier = [b for b in bars[:-1] if (last.day - b.day).days >= days]
        prev = earlier[-1] if days > 1 and earlier else bars[-2]
        pct = (last.close / prev.close - 1) * 100
        item_key = f"price:{key[0]}:{key[1]}:{last.day.isoformat()}"
        if abs(pct) < threshold or item_key in already_sent:
            continue
        period = "on" if days == 1 else "in the week to"
        change = units * (last.close - prev.close)
        out.append((item_key,
                    f"{key[0]} {'up' if pct > 0 else 'down'} {abs(pct):.1f}% {period} "
                    f"{last.day:%a %d %b}, to {last.close:,.2f} {currency[key]} "
                    f"({change:+,.0f} {currency[key]} on your {units:g} units)"))
    return out


def months_until(today: date, target: date) -> int:
    months = (target.year - today.year) * 12 + target.month - today.month
    return months - (1 if target.day < today.day else 0)


def deadline_reminders(categories: list[Category], progress: dict[UUID, GoalProgress],
                       base: str, already_sent: set[str], today: date
                       ) -> list[tuple[str, Category, str, str]]:
    """(key, category, title, body) for the nearest milestone each goal has passed, once."""
    out = []
    for cat in categories:
        if not cat.target_date or cat.target_date <= today:
            continue
        passed = [m for m in DEADLINE_MONTHS if months_until(today, cat.target_date) < m]
        if not passed:
            continue
        milestone = min(passed)
        key = f"deadline:{cat.id}:{milestone}"
        if key in already_sent:
            continue
        when = ("1 month" if milestone == 1 else "1 year" if milestone == 12 else
                f"{milestone // 12} years" if milestone > 12 else f"{milestone} months")
        body = f"The target date is {cat.target_date:%d/%m/%Y}."
        p = progress.get(cat.id)
        if p is not None and p.target:
            body += f" It holds {p.value:,.0f} of the {p.target:,.0f} {base} target."
            if p.on_track is False and p.monthly_needed:
                body += (f" At the assumed return it's behind: about {p.monthly_needed:,.0f} "
                         f"{base} a month would close the gap.")
            elif p.on_track:
                body += " At the assumed return it's on track."
        body += " As the date gets close, new money is safest in cash."
        out.append((key, cat, f"Less than {when} to go", body))
    return out


# Messages ------------------------------------------------------------------------

@dataclass
class Message:
    """One in-app notification; the email combines them all."""

    kind: str                     # 'recommendation', 'health', 'price_move' or 'deadline'
    title: str
    body: str
    category_id: UUID | None = None
    goal: str | None = None       # goal name, for the email's headings
    payload: dict = field(default_factory=dict)


def goal_message(category: Category, change: Change) -> Message:
    lines = []
    if change.added:
        lines.append("New suggestions: " + ", ".join(change.added) + ".")
    if change.removed:
        lines.append("No longer suggested: " + ", ".join(change.removed) + ".")
    if change.reweighted:
        lines.append("Bigger changes in weight: " + ", ".join(change.reweighted) + ".")
    for f in change.new_findings:
        label = "Alert" if f["severity"] == Severity.ALERT.value else "Warning"
        lines.append(f"{label}: {f['title']}.")
    if change.cleared:
        lines.append("Good news, now resolved: " + "; ".join(change.cleared) + ".")
    title = ("Recommendations changed" if change.picks_changed else
             f"{len(change.new_findings)} new health check "
             f"{'issue' if len(change.new_findings) == 1 else 'issues'}")
    return Message("recommendation" if change.picks_changed else "health", title,
                   " ".join(lines), category.id, category.name,
                   {"added": change.added, "removed": change.removed,
                    "reweighted": change.reweighted, "new_findings": change.new_findings,
                    "cleared": change.cleared})


def portfolio_message(change: Change) -> Message:
    lines = []
    for f in change.new_findings:
        label = "Alert" if f["severity"] == Severity.ALERT.value else "Warning"
        lines.append(f"{label}: {f['title']}.")
    if change.cleared:
        lines.append("Good news, now resolved: " + "; ".join(change.cleared) + ".")
    n = len(change.new_findings)
    return Message("health", f"{n} new portfolio {'issue' if n == 1 else 'issues'}",
                   " ".join(lines), payload={"new_findings": change.new_findings,
                                             "cleared": change.cleared})


def price_message(moves: list[tuple[str, str]]) -> Message:
    return Message("price_move", "Big price moves" if len(moves) > 1 else "A big price move",
                   " ".join(line + "." for _, line in moves),
                   payload={"keys": [k for k, _ in moves]})


def compose_email(messages: list[Message], name: str | None, app_url: str
                  ) -> tuple[str, str, str]:
    """Subject, plain text and HTML for one email combining all of a person's messages."""
    goals = sorted({m.goal for m in messages if m.goal})
    if len(messages) == 1:
        m = messages[0]
        subject = f"{m.goal}: {m.title}" if m.goal else m.title
    else:
        subject = f"{len(messages)} updates" + (f" for {', '.join(goals)}" if goals else "")
    subject = f"Share Recommender: {subject}"
    hello = f"Hi {name}," if name else "Hi,"
    footer = (f"{DISCLAIMER} To change how often you get these, or stop them, open the "
              "Profile page in the app.")

    text = [hello, "", "Here's what's new in your portfolio:", ""]
    for m in messages:
        heading = f"{m.goal}: {m.title}" if m.goal else m.title
        text += [heading.upper(), m.body, ""]
    text += [f"Open the app: {app_url}", "", footer]

    e = html.escape
    parts = [f"<p>{e(hello)}</p><p>Here's what's new in your portfolio:</p>"]
    for m in messages:
        heading = f"{m.goal}: {m.title}" if m.goal else m.title
        parts.append(f"<h3 style='margin:20px 0 6px'>{e(heading)}</h3><p style='margin:0'>"
                     f"{e(m.body)}</p>")
    parts.append(f"<p style='margin-top:20px'><a href='{e(app_url)}'>Open Share Recommender</a>"
                 f"</p><p style='color:#898781;font-size:12px'>{e(footer)}</p>")
    body_html = ("<div style='font-family:system-ui,-apple-system,Segoe UI,sans-serif;"
                 "max-width:600px;color:#0b0b0b'>" + "".join(parts) + "</div>")
    return subject, "\n".join(text), body_html
