"""The scheduled notification job, and the service-key repository it uses."""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

from share_recommender.core.models import Category, Holding, Profile
from share_recommender.data.base import PriceBar
from share_recommender.db.admin import AdminRepository, list_people
from share_recommender.jobs.notify import is_due, run
from tests.fakes import FakeProvider

MONDAY, TUESDAY, SATURDAY = date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 7)


# A service-key repository that records every filter ---------------------------

class RecordingQuery:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.op, self.payload = db, table, [], "select", None

    def select(self, *_):
        return self

    def eq(self, column, value):
        self.filters.append((column, str(value)))
        return self

    def in_(self, column, values):
        self.filters.append((column, tuple(values)))
        return self

    def order(self, *_, **__):
        return self

    def limit(self, *_):
        return self

    def delete(self):
        self.op = "delete"
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def insert(self, rows):
        self.op, self.payload = "insert", rows
        return self

    def upsert(self, rows):
        self.op, self.payload = "upsert", rows
        return self

    def execute(self):
        self.db.calls.append(self)
        if self.op == "insert":
            return SimpleNamespace(data=[{"id": str(uuid4())} for _ in self.payload])
        return SimpleNamespace(data=[])


class RecordingClient:
    def __init__(self):
        self.calls = []

    def table(self, name):
        return RecordingQuery(self, name)


def test_admin_repository_always_filters_by_person():
    from share_recommender.core.notifications import Message

    client, me = RecordingClient(), uuid4()
    repo = AdminRepository(client, me)
    repo.profile()
    repo.preferences()
    repo.categories()
    repo.holdings()
    repo.cash_accounts()
    repo.snapshots()
    repo.mark_emailed(["n1"])
    repo.watchlist()
    repo.latest_pick_run()
    repo.picks("run1")
    repo.prune_pick_runs()
    for q in client.calls:
        column = "id" if q.table == "profiles" else "user_id"
        assert (column, str(me)) in q.filters, (q.table, q.op, q.filters)

    # Writes put the person's id on every row.
    from share_recommender.core.picker import PickRow, PickRun

    repo.save_snapshots({"portfolio": {"picks": {}}})
    repo.add_notifications([Message("health", "t", "b")])
    repo.save_pick_run(PickRun(trigger="scheduled"), [PickRow(
        "", "CBA", "ASX", None, "share", "buy", "high", 1, None, None, "r", None, {}, "screen")])
    for q in client.calls[-4:]:
        rows = q.payload if isinstance(q.payload, list) else [q.payload]
        assert all(row["user_id"] == str(me) for row in rows), q.table


def test_list_people_pages_through_everyone():
    users = [SimpleNamespace(id=uuid4(), email=f"p{i}@x.com") for i in range(150)]
    client = SimpleNamespace(auth=SimpleNamespace(admin=SimpleNamespace(
        list_users=lambda page, per_page: users[(page - 1) * per_page:page * per_page])))
    assert len(list_people(client)) == 150


def test_is_due():
    assert is_due("daily", TUESDAY, False) and not is_due("daily", SATURDAY, False)
    assert is_due("weekly", MONDAY, False) and not is_due("weekly", TUESDAY, False)
    assert is_due("weekly", TUESDAY, True)
    assert not is_due("off", MONDAY, True)  # off is always off


# The job against in-memory repositories ---------------------------------------

class FakeAdminRepo:
    def __init__(self, user_id, frequency="daily", email_on=True, fail=False,
                 picker="weekly", answers=False):
        self.user_id = user_id
        self.profile_ = Profile(id=user_id, display_name=f"P{str(user_id)[:4]}",
                                notify_frequency=frequency, notify_email=email_on,
                                picker_frequency=picker)
        self.answers = answers  # interview done, so the picker can run
        self.runs, self.saved_picks = [], {}
        self.goals = [Category(id=uuid4(), user_id=user_id, name="Kids fund"),
                      Category(id=uuid4(), user_id=user_id, name="House")]
        self.holdings_ = [self._holding("VAS", self.goals[0]), self._holding("VGS", self.goals[1])]
        self.stored: dict[str, dict] = {}
        self.notifications, self.emailed = [], []
        self.fail = fail

    def _holding(self, ticker, goal):
        return Holding(id=uuid4(), user_id=self.user_id, ticker=ticker, exchange="ASX",
                       units="10", buy_price="100", buy_date=date(2026, 1, 5),
                       category_id=goal.id)

    def profile(self):
        if self.fail:
            raise RuntimeError("database unavailable")
        return self.profile_

    def preferences(self):
        from share_recommender.core.preferences import InvestorPreferences

        return InvestorPreferences(markets=["ASX"]) if self.answers else None

    def categories(self):
        from share_recommender.core.preferences import CategoryPreferences

        prefs = CategoryPreferences(horizon_years=10)
        return self.goals, ({g.id: prefs for g in self.goals} if self.answers else {})

    def watchlist(self):
        return []

    def latest_pick_run(self):
        return self.runs[-1] if self.runs else None

    def picks(self, run_id):
        return self.saved_picks.get(str(run_id), [])

    def save_pick_run(self, run, picks):
        run = run.model_copy(update={"id": uuid4()})
        self.runs.append(run)
        self.saved_picks[str(run.id)] = picks
        return str(run.id)

    def prune_pick_runs(self, keep=10):
        self.runs = self.runs[-keep:]

    def holdings(self):
        return self.holdings_

    def cash_accounts(self):
        return []

    def snapshots(self):
        return dict(self.stored)

    def save_snapshots(self, snapshots):
        self.stored.update(snapshots)

    def add_notifications(self, messages):
        self.notifications += messages
        return [f"id{i}" for i, _ in enumerate(messages)]

    def mark_emailed(self, ids):
        self.emailed += ids


class Outbox:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, text, html):
        self.sent.append(SimpleNamespace(to=to, subject=subject, text=text))


class FlatPrices(FakeProvider):
    def __init__(self):
        super().__init__(price_everything=True)

    def get_history(self, ticker, exchange, start, end):
        return [PriceBar(start + timedelta(days=i), Decimal(100))
                for i in range((end - start).days + 1)]


def _run(repos, outbox, today=TUESDAY, **kw):
    people = [(r.user_id, f"{r.profile_.display_name}@x.com") for r in repos.values()]
    return run(people, repos.__getitem__, FlatPrices(), outbox, today=today,
               app_url="http://app", **kw)


def _change_both_goals(repo):
    for goal in repo.goals:  # pretend last time each goal recommended something else
        repo.stored[str(goal.id)] = {"picks": {"ZZZ:ASX": "100"}, "findings": {}, "sent": []}


def test_two_people_each_get_only_their_own_email():
    ian, partner = uuid4(), uuid4()
    repos = {ian: FakeAdminRepo(ian), partner: FakeAdminRepo(partner)}
    outbox = Outbox()

    assert _run(repos, outbox) == 0  # first run: baselines only
    assert outbox.sent == [] and all(r.stored for r in repos.values())

    _change_both_goals(repos[ian])
    assert _run(repos, outbox) == 0
    (email,) = outbox.sent  # only Ian had changes
    assert email.to == f"{repos[ian].profile_.display_name}@x.com"
    assert "Kids fund" in email.subject and "House" in email.subject
    goal_notes = [m for m in repos[ian].notifications if m.goal]
    assert {m.goal for m in goal_notes} == {"Kids fund", "House"}  # two notifications
    assert repos[ian].emailed and not repos[partner].notifications
    for m in repos[ian].notifications:  # nothing about the other person's goals
        assert m.category_id in {g.id for g in repos[ian].goals} or m.category_id is None


def test_frequency_email_setting_dry_run_and_failures():
    daily, weekly, off, quiet, broken = (uuid4() for _ in range(5))
    repos = {daily: FakeAdminRepo(daily, "daily"), weekly: FakeAdminRepo(weekly, "weekly"),
             off: FakeAdminRepo(off, "off"), quiet: FakeAdminRepo(quiet, email_on=False),
             broken: FakeAdminRepo(broken, fail=True)}
    outbox = Outbox()

    # One person failing doesn't stop the others; the job reports the failure.
    assert _run(repos, outbox, today=TUESDAY) == 1
    assert repos[daily].stored and repos[quiet].stored
    assert not repos[weekly].stored and not repos[off].stored  # not due on a Tuesday

    for r in repos.values():
        _change_both_goals(r)
    outbox.sent.clear()
    _run(repos, outbox, today=TUESDAY, dry_run=True)
    assert outbox.sent == [] and not repos[daily].notifications  # dry run: nothing written

    _run(repos, outbox, today=TUESDAY)
    assert [e.to.split("@")[0] for e in outbox.sent] == [repos[daily].profile_.display_name]
    assert repos[quiet].notifications and not repos[quiet].emailed  # in-app only
    assert not repos[off].notifications


def test_only_one_person_with_user_flag():
    a, b = uuid4(), uuid4()
    repos = {a: FakeAdminRepo(a), b: FakeAdminRepo(b)}
    _run(repos, Outbox(), only=f"{repos[b].profile_.display_name}@X.com")
    assert repos[b].stored and not repos[a].stored
    assert isinstance(a, UUID)


# The weekly AI picker inside the job --------------------------------------------

def _picker_repos(**kw):
    a = uuid4()
    return {a: FakeAdminRepo(a, answers=True, **kw)}


def test_the_picker_runs_for_weekly_people_on_mondays():
    from tests.fakes import StubRanker

    repos = _picker_repos()
    (repo,) = repos.values()
    rank = StubRanker()
    _run(repos, Outbox(), today=TUESDAY, rank_fn=rank)
    assert rank.payloads == [] and repo.runs == []      # not Monday
    _run(repos, Outbox(), today=MONDAY, rank_fn=rank)
    assert len(rank.payloads) == 1 and repo.runs[-1].trigger == "scheduled"
    _run(repos, Outbox(), today=TUESDAY, rank_fn=rank, force=True)
    assert len(repo.runs) >= 1                           # --force runs it any day

    off = _picker_repos(picker="off")
    rank = StubRanker()
    _run(off, Outbox(), today=MONDAY, rank_fn=rank)
    assert rank.payloads == []


def test_the_picker_is_skipped_without_a_key_or_with_no_picker(caplog):
    repos = _picker_repos()
    (repo,) = repos.values()
    assert _run(repos, Outbox(), today=MONDAY, rank_fn=None) == 0
    assert repo.runs == [] and "no ANTHROPIC_API_KEY" in caplog.text

    from tests.fakes import StubRanker
    rank = StubRanker()
    _run(repos, Outbox(), today=MONDAY, rank_fn=rank, picker=False)
    assert rank.payloads == []


def test_a_picker_failure_still_sends_notifications():
    from share_recommender.ai.client import AIUnavailable
    from tests.fakes import StubRanker

    repos = _picker_repos(frequency="weekly")
    (repo,) = repos.values()
    _run(repos, Outbox(), today=MONDAY, picker=False)  # baseline
    _change_both_goals(repo)
    outbox = Outbox()
    failures = _run(repos, outbox, today=MONDAY, rank_fn=StubRanker(AIUnavailable("busy")))
    assert failures == 1                   # counted, so the job exits non-zero
    assert repo.notifications and outbox.sent  # but the email still went


def test_dry_run_runs_the_picker_but_saves_nothing(capsys):
    from tests.fakes import StubRanker

    repos = _picker_repos()
    (repo,) = repos.values()
    rank = StubRanker()
    _run(repos, Outbox(), today=MONDAY, rank_fn=rank, dry_run=True)
    assert len(rank.payloads) == 1 and repo.runs == [] and not repo.stored
    assert "AI pick 1. buy" in capsys.readouterr().out
