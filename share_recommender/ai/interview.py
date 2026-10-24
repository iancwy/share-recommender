"""Chat interview: Claude asks the questionnaire conversationally and fills in a draft.

Every turn returns Claude's reply plus a complete draft of the same answers the form
collects (``core.questionnaire``), as structured output. Nothing is saved here: the person
reviews the draft and confirms it in the app. Anything relevant that doesn't fit a question
goes into the free-text ``notes`` fields, so nuance isn't lost.
"""

import json
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError

from share_recommender.ai.client import FALLBACK, MODEL, AIUnavailable, client, friendly_errors
from share_recommender.core.exchanges import EXCHANGES
from share_recommender.core.models import Category
from share_recommender.core.preferences import (
    CategoryPreferences,
    DirectShares,
    DrawdownReaction,
    Experience,
    Flexibility,
    GoalStyle,
    HomeBias,
    IncomeGrowth,
    InvestorPreferences,
)
from share_recommender.core.questionnaire import ABOUT_YOU, GOAL, describe
from share_recommender.core.universe import Exclusion, Theme

GREETING = ("Hi! I'll ask you a few questions about yourself and each of your goals, then "
            "fill in your preferences for you to check. Answer in your own words, and mention "
            "anything else you think matters. To start: how long have you been investing, and "
            "what are you investing for?")

MarketCode = StrEnum("MarketCode", {code: code for code in EXCHANGES})

SYSTEM = f"""\
You are the interviewer for a personal share and ETF recommender used by an everyday investor \
in Australia. Your job is to fill in their preferences questionnaire by chatting with them. A \
rules-based tool then makes recommendations from the saved answers, so the answers must be \
accurate.

The person has already been greeted with: "{GREETING}"

How to interview:
- Ask about one or two questions at a time, conversationally. Use plain words; never show \
field names or option codes. Keep each reply under about 120 words.
- Map what they say to the allowed values. If an answer is ambiguous, ask a short follow-up \
rather than guess. Briefly explain a question if they seem unsure.
- Capture nuance. Anything relevant that the questions don't cover (plans, constraints, \
worries, existing assets, who a goal is for) goes into a notes field in a sentence or two, in \
their terms: about_you.notes for the person, or the goal's notes. If a nuance also maps to a \
field, fill both: for example "I get lots of CBA shares through work" means avoid_tickers CBA \
plus a note.
- Cover every question about them, then each goal in the list provided. Skip horizon_years \
for goals that have a target date. Ask about themes only for speculative goals.
- Do not recommend investments, predict returns or give financial advice; the tool does the \
recommending afterwards. If asked, say so and carry on.

Output, every turn:
- reply: your next message to them.
- about_you and goals: the complete draft so far. Start from the current draft you are given, \
keep every answer already given, and change only what the person has just told you. Leave a \
field null if it hasn't come up and there's no saved answer to keep. Saved answers count as \
answers: confirm or update them rather than asking from scratch.
- goals: one entry per goal discussed, with category set to the goal's exact name from the \
list.
- complete: true once every question has an answer. Then tell them their answers are ready \
to review and save below the chat, and invite any changes.

Questions about the person (about_you):
{describe(ABOUT_YOU)}

Questions for each goal (goals):
{describe(GOAL)}"""


class AboutYouDraft(BaseModel):
    experience: Experience | None = None
    drawdown_reaction: DrawdownReaction | None = None
    markets: list[MarketCode] | None = None
    home_bias: HomeBias | None = None
    direct_shares: DirectShares | None = None
    income_growth: IncomeGrowth | None = None
    exclusions: list[Exclusion] | None = None
    avoid_tickers: list[str] | None = None
    brokerage_per_trade: float | None = None
    notes: str | None = None


class GoalDraft(BaseModel):
    category: str = Field(description="Exact name of one of the person's goals")
    horizon_years: int | None = None
    flexibility: Flexibility | None = None
    style: GoalStyle | None = None
    themes: list[Theme] | None = None
    notes: str | None = None


class InterviewTurn(BaseModel):
    reply: str
    about_you: AboutYouDraft
    goals: list[GoalDraft]
    complete: bool


@dataclass
class Draft:
    """Answers so far, already merged onto the saved ones and validated."""

    about_you: InvestorPreferences
    goals: dict[UUID, CategoryPreferences]
    complete: bool = False
    # Fields the chat has filled in, so defaults aren't mistaken for answers.
    answered: set[str] = field(default_factory=set)
    goal_answered: dict[UUID, set[str]] = field(default_factory=dict)


@dataclass
class TurnResult:
    reply: str
    draft: Draft
    problems: list[str]  # draft answers that couldn't be used, for the person to fix


def _merge(model: type[BaseModel], base: BaseModel | None, draft: BaseModel,
           problems: list[str], label: str):
    """Saved answers overlaid with every non-null draft answer that validates."""
    data = (base or model()).model_dump()
    used = set()
    for name, value in draft.model_dump(exclude_none=True).items():
        if name not in model.model_fields:
            continue
        try:
            data = model.model_validate(data | {name: value}).model_dump()
            used.add(name)
        except ValidationError as exc:
            problems.append(f"{label} {name.replace('_', ' ')}: {exc.errors()[0]['msg']}")
    return model.model_validate(data), used


def apply_turn(turn: InterviewTurn, categories: list[Category], previous: Draft) -> TurnResult:
    """Merge Claude's draft onto the previous draft (or saved answers), keeping what validates."""
    problems: list[str] = []
    about_you, used = _merge(InvestorPreferences, previous.about_you, turn.about_you, problems,
                             "About you:")
    by_name = {c.name.casefold(): c for c in categories}
    goals = dict(previous.goals)
    goal_answered = {cid: set(f) for cid, f in previous.goal_answered.items()}
    for g in turn.goals:
        cat = by_name.get(g.category.strip().casefold())
        if cat is None:
            problems.append(f"Ignored answers for an unknown goal, '{g.category}'.")
            continue
        goals[cat.id], goal_used = _merge(CategoryPreferences, previous.goals.get(cat.id), g,
                                          problems, f"{cat.name}:")
        goal_answered.setdefault(cat.id, set()).update(goal_used)
    draft = Draft(about_you, goals, turn.complete, previous.answered | used, goal_answered)
    return TurnResult(turn.reply, draft, problems)


def _context(categories: list[Category], saved_about_you: InvestorPreferences | None,
             saved_goals: dict[UUID, CategoryPreferences], draft: Draft | None) -> str:
    def goal_info(c: Category) -> dict:
        return {"name": c.name, "risk_level": c.risk_level.value,
                "target_date": c.target_date.isoformat() if c.target_date else None,
                "target_amount": str(c.target_amount) if c.target_amount else None,
                "description": c.description}

    names = {c.id: c.name for c in categories}
    return json.dumps({
        "goals": [goal_info(c) for c in categories],
        "saved_answers": {
            "about_you": saved_about_you.model_dump(mode="json") if saved_about_you else None,
            "goals": {names[cid]: p.model_dump(mode="json") for cid, p in saved_goals.items()
                      if cid in names},
        },
        "current_draft": None if draft is None else {
            "about_you": draft.about_you.model_dump(mode="json"),
            "goals": {names[cid]: p.model_dump(mode="json") for cid, p in draft.goals.items()
                      if cid in names},
        },
    }, indent=1)


def run_turn(history: list[dict], message: str, categories: list[Category],
             saved_about_you: InvestorPreferences | None,
             saved_goals: dict[UUID, CategoryPreferences], draft: Draft | None) -> TurnResult:
    """One exchange. ``history`` is earlier {"role", "content"} text messages, oldest first."""
    context = _context(categories, saved_about_you, saved_goals, draft)
    messages = history + [{"role": "user", "content": [
        {"type": "text", "text": f"Context (not from the person):\n{context}"},
        {"type": "text", "text": f"The person says:\n{message}"},
    ]}]
    with friendly_errors():
        response = client().beta.messages.parse(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            output_config={"effort": "medium"},
            output_format=InterviewTurn,
            cache_control={"type": "ephemeral"},
            messages=messages,
            **FALLBACK,
        )
    turn = response.parsed_output
    if response.stop_reason == "refusal" or turn is None:
        raise AIUnavailable("Claude couldn't continue the interview. Try rephrasing, or use "
                            "the form instead.")
    base = draft or Draft(saved_about_you or InvestorPreferences(), dict(saved_goals))
    return apply_turn(turn, categories, base)
