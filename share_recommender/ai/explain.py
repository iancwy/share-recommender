"""Ask Claude to explain a recommendation in plain English.

The rules in ``core.recommender`` decide everything; Claude only rewrites their output and
reasons as a short explanation. It is told not to add tickers, numbers or advice of its own.
"""

import json

from share_recommender.ai.client import FALLBACK, MODEL, AIUnavailable, client, friendly_errors
from share_recommender.core.models import Category
from share_recommender.core.preferences import CategoryPreferences, InvestorPreferences
from share_recommender.core.recommender import Recommendation

SYSTEM = """\
You explain investment recommendations produced by a rules-based tool to the person they \
are for, an everyday investor in Australia. You receive as JSON the person's interview \
answers, the goal itself (a target date, if set, is what sets the time horizon), and the \
tool's output: a target mix for that goal, the ETFs and shares picked for each slice with \
the tool's reasons, how current holdings compare, and possibly a buy plan.

Write a clear explanation of about 200-350 words in plain English:
- Start with the big picture: how much is in shares versus defensive assets, and why, \
given the goal, its time horizon and the person's answers.
- Then walk through the slices and picks, and the biggest gaps, if any.
- If there is a buy plan, say in a sentence or two what it does.
- Mention any warnings the tool raised.
- The answers may include free-text notes from the person (interview_answers.notes and \
goal_answers.notes). The tool can't read these, so check them against the recommendation: \
point out anything a note bears on, such as a pick or a timeline that seems to conflict with \
what they said, and suggest they adjust their answers if so.

Rules:
- Use only the facts, tickers and numbers in the JSON. Do not add investments, forecasts, \
return figures or advice of your own. Apart from flagging conflicts with their notes, do not \
contradict the tool.
- Short paragraphs or a few bullet points; no headings. Amounts are in the base currency given.
- Finish with one sentence saying this is general information from a rules-based tool, not \
personal financial advice."""


def explain(rec: Recommendation, inv: InvestorPreferences, prefs: CategoryPreferences,
            category: Category | None = None) -> str:
    facts = {
        "interview_answers": inv.model_dump(mode="json"),
        "goal": category.model_dump(mode="json", include={
            "name", "description", "risk_level", "target_amount", "target_date"})
        if category else {"name": rec.category_name},
        "goal_answers": prefs.model_dump(mode="json"),
        "recommendation": rec.model_dump(mode="json", exclude={"category_id"}),
    }
    with friendly_errors():
        response = client().beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            output_config={"effort": "medium"},
            messages=[{"role": "user", "content": json.dumps(facts, indent=1)}],
            **FALLBACK,
        )
    if response.stop_reason == "refusal":
        raise AIUnavailable("Claude declined to write an explanation for this one.")
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise AIUnavailable("Claude returned an empty explanation. Try again.")
    return text
