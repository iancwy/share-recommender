"""Predefined goal categories offered when a user adds a category.

Users can also create their own. The signup trigger in the database creates
"General investing" for every new user so the holdings form works straight away.
"""

from share_recommender.core.models import RiskLevel

CATEGORY_TEMPLATES: list[dict] = [
    {"name": "General investing", "risk_level": RiskLevel.MEDIUM,
     "description": "Long-term wealth building with no fixed deadline."},
    {"name": "Kids private school investment fund", "risk_level": RiskLevel.MEDIUM,
     "description": "Saving towards school fees by a target date."},
    {"name": "Retirement", "risk_level": RiskLevel.MEDIUM,
     "description": "Long horizon, growth focused."},
    {"name": "House deposit", "risk_level": RiskLevel.LOW,
     "description": "Shorter horizon, capital preservation matters."},
    {"name": "Emergency buffer", "risk_level": RiskLevel.LOW,
     "description": "Money that may be needed at short notice."},
    {"name": "Speculative", "risk_level": RiskLevel.HIGH,
     "description": "Small, high-risk positions."},
]
