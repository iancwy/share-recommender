"""Today's date where Ian lives. The servers (Streamlit Cloud, GitHub Actions) run on UTC, so
before about 11am in Sydney ``date.today()`` there is still yesterday."""

from datetime import UTC, date, datetime, timedelta


def sydney_today() -> date:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Australia/Sydney")).date()
    except Exception:  # no time zone database (e.g. Windows without tzdata): close enough
        return (datetime.now(UTC) + timedelta(hours=10)).date()
