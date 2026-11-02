from datetime import date
from decimal import Decimal
from uuid import uuid4

from share_recommender.core.health import Severity
from share_recommender.core.models import Category, Holding, RiskLevel
from share_recommender.core.preferences import CategoryPreferences, InvestorPreferences
from share_recommender.portfolio_service import health_report
from share_recommender.recommendations import provider_fns
from tests.fakes import USER_ID, FakeProvider

TODAY = date(2026, 10, 30)


def _h(ticker, units, category=None, platform="CMC Markets", fees="0", exchange="ASX"):
    return Holding(id=uuid4(), user_id=USER_ID, ticker=ticker, exchange=exchange, units=units,
                   buy_price="100", buy_date=date(2026, 1, 1), fees=fees, platform=platform,
                   category_id=category.id if category else None)


DEFAULT = InvestorPreferences()


def _report(categories, holdings, inv=DEFAULT, prefs=None, provider=None):
    provider = provider or FakeProvider(price_everything=True)
    return health_report(categories, holdings, inv, prefs or {}, "AUD", *provider_fns(provider),
                         today=TODAY)


def _titles(report, severity=None):
    return [f.title for f in report.findings if severity is None or f.severity is severity]


def test_drift_is_flagged_with_a_top_up_fix():
    goal = Category(id=uuid4(), user_id=USER_ID, name="General investing")
    # All in Australian shares against a 70/30 mix of shares and cash.
    report = _report([goal], [_h("VAS", "50", goal)])
    alert = next(f for f in report.findings if f.area == goal.name
                 and f.severity is Severity.ALERT)
    assert alert.title.startswith("Off its target mix")
    assert "New money" in alert.action or "new money" in alert.action
    assert "sell" not in alert.action.lower()
    assert report.findings[0].severity is Severity.ALERT  # sorted worst first


def test_balanced_goal_is_ok_and_near_deadline_with_too_many_shares_alerts():
    goal = Category(id=uuid4(), user_id=USER_ID, name="House deposit", risk_level=RiskLevel.LOW,
                    target_date=date(2027, 12, 1), target_amount=Decimal(100000))
    report = _report([goal], [_h("VAS", "50", goal)])
    titles = _titles(report)
    assert "Close to its deadline with too much in shares" in titles
    assert "Behind its target" in titles

    general = Category(id=uuid4(), user_id=USER_ID, name="General investing")
    prefs = {general.id: CategoryPreferences(horizon_years=15)}
    inv = InvestorPreferences(direct_shares="etf_only")
    mix = [_h("VAS", "23", general), _h("VGS", "40", general), _h("VGE", "7", general),
           _h("AAA", "30", general)]
    assert "On its target mix" in _titles(_report([general], mix, inv, prefs), Severity.OK)


def test_concentration_fees_platforms_and_data_quality():
    holdings = [_h("CBA", "30", fees="50"), _h("VAS", "70", platform=None),
                _h("MYSTERY", "1", platform="CommSec", fees="9")]
    provider = FakeProvider(prices={("CBA", "ASX"): "100", ("VAS", "ASX"): "100"})
    report = _report([], holdings, inv=None, provider=provider)
    titles = _titles(report)
    assert any(t.startswith("29.7% in one company (CBA)") for t in titles)
    assert "Holdings without a current price" in titles
    assert "3 holding(s) not in a goal" in titles
    assert "Preferences not answered" in titles
    assert "1 holding(s) without a platform" in titles
    assert "Brokerage is 8.26% of what you invested" in titles  # 9 on 109 paid, at CommSec
    cmc = next(p for p in report.platforms if p.platform == "CMC Markets")
    assert cmc.fees == 50 and cmc.fees_pct == Decimal("1.64")  # 50 on 3,050 paid


def test_empty_goal_is_info():
    goal = Category(id=uuid4(), user_id=USER_ID, name="Retirement")
    report = _report([goal], [_h("VAS", "1")])
    assert "Nothing held yet" in _titles(report, Severity.INFO)
