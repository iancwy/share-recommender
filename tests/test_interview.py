"""The questionnaire definition and the chat interview's handling of Claude's drafts."""

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from share_recommender.ai import interview
from share_recommender.ai.interview import (
    AboutYouDraft,
    Draft,
    GoalDraft,
    InterviewTurn,
    apply_turn,
    run_turn,
)
from share_recommender.core.models import Category, RiskLevel
from share_recommender.core.preferences import (
    CategoryPreferences,
    DirectShares,
    Experience,
    GoalStyle,
    InvestorPreferences,
)
from share_recommender.core.questionnaire import ABOUT_YOU, GOAL
from share_recommender.core.universe import Theme
from tests.fakes import USER_ID

KIDS = Category(id=uuid4(), user_id=USER_ID, name="Kids school fund")
SPEC = Category(id=uuid4(), user_id=USER_ID, name="Speculative", risk_level=RiskLevel.HIGH)


def test_questionnaire_covers_every_saved_field():
    assert {q.field for q in ABOUT_YOU} == set(InvestorPreferences.model_fields)
    assert {q.field for q in GOAL} == set(CategoryPreferences.model_fields)
    assert {q.field for q in ABOUT_YOU} == set(AboutYouDraft.model_fields)
    assert {q.field for q in GOAL} | {"category"} == set(GoalDraft.model_fields)
    assert "avoid_tickers" in interview.SYSTEM and "Allowed values" in interview.SYSTEM


def _turn(about_you=None, goals=(), complete=False):
    return InterviewTurn(reply="Thanks!", about_you=about_you or AboutYouDraft(),
                         goals=list(goals), complete=complete)


def test_apply_turn_merges_onto_saved_answers_and_tracks_answers():
    saved = Draft(InvestorPreferences(experience=Experience.EXPERIENCED, markets=["ASX"]), {})
    turn = _turn(AboutYouDraft(direct_shares=DirectShares.MIX, avoid_tickers=["cba"],
                               notes="Gets CBA shares through work."),
                 [GoalDraft(category="kids school FUND", horizon_years=7,
                            notes="Fees start in 2033 for two kids.")])
    result = apply_turn(turn, [KIDS, SPEC], saved)
    d = result.draft
    assert d.about_you.experience is Experience.EXPERIENCED  # kept from saved
    assert d.about_you.markets == ["ASX"]
    assert d.about_you.direct_shares is DirectShares.MIX
    assert d.about_you.avoid_tickers == ["CBA"]
    assert d.about_you.notes == "Gets CBA shares through work."
    assert d.answered == {"direct_shares", "avoid_tickers", "notes"}
    assert d.goals[KIDS.id].horizon_years == 7 and d.goal_answered[KIDS.id] == {
        "horizon_years", "notes"}
    assert result.problems == []


def test_apply_turn_keeps_previous_answers_when_values_are_bad():
    previous = Draft(InvestorPreferences(), {}, answered={"markets"})
    turn = _turn(AboutYouDraft(markets=[], brokerage_per_trade=-5),
                 [GoalDraft(category="Holiday", horizon_years=2),
                  GoalDraft(category="Speculative", style=GoalStyle.SPECULATIVE,
                            themes=[Theme.BIOTECH])])
    result = apply_turn(turn, [KIDS, SPEC], previous)
    assert result.draft.about_you == InvestorPreferences()
    assert len(result.problems) == 3  # empty markets, negative brokerage, unknown goal
    assert result.draft.goals[SPEC.id].themes == [Theme.BIOTECH]
    assert result.draft.answered == {"markets"}


def test_run_turn_sends_context_and_parses_the_draft():
    parsed = _turn(AboutYouDraft(experience=Experience.BEGINNER), complete=False)
    response = SimpleNamespace(parsed_output=parsed, stop_reason="end_turn")
    fake_client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(
        parse=lambda **kw: fake_client.calls.append(kw) or response)), calls=[])
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Hello"}]

    with patch.object(interview, "client", return_value=fake_client):
        result = run_turn(history, "I'm new to this", [KIDS], None, {}, None)

    (call,) = fake_client.calls
    assert call["model"] == "claude-opus-5-5"
    assert call["output_format"] is InterviewTurn
    assert call["fallbacks"] == "default"
    assert call["messages"][:2] == history
    context, said = call["messages"][2]["content"]
    assert "Kids school fund" in context["text"] and said["text"].endswith("I'm new to this")
    assert result.draft.about_you.experience is Experience.BEGINNER
    assert result.reply == "Thanks!"


def test_run_turn_refusal_is_reported():
    response = SimpleNamespace(parsed_output=None, stop_reason="refusal")
    fake_client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(
        parse=lambda **kw: response)))
    with patch.object(interview, "client", return_value=fake_client):
        try:
            run_turn([], "hi", [], None, {}, None)
        except interview.AIUnavailable as exc:
            assert "form instead" in str(exc)
        else:
            raise AssertionError("expected AIUnavailable")


def test_missing_api_key_is_a_friendly_error(monkeypatch):
    from share_recommender.config import Settings

    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(interview, "client", lambda: __import__("anthropic").Anthropic(
        api_key=None, auth_token=None))
    monkeypatch.setattr("share_recommender.ai.client.get_settings",
                        lambda: Settings(_env_file=None))
    try:
        run_turn([], "hi", [], None, {}, None)
    except interview.AIUnavailable as exc:
        assert "ANTHROPIC_API_KEY" in str(exc)
    else:
        raise AssertionError("expected AIUnavailable")
