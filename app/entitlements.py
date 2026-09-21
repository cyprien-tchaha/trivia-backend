"""
What a host is allowed to do.

The free/paid line follows the cost line the architecture already draws:
a banked topic is pre-generated and costs nothing to serve, while any other
topic means live AI generation on every game. So free hosts — signed in or
not — play the banked topics, and pro hosts can ask for any title.

ENFORCE_ENTITLEMENTS defaults to FALSE and must stay that way until billing
ships. Turning the check on before there is a way to pay would take a working
feature away from every existing host and offer them nothing in return. The
code is here so the plumbing is testable and the flip is one variable.
"""
from typing import Optional
import os

from app.models import User
from app.services.question_bank_service import match_bank_topic


def enforcement_on() -> bool:
    return os.getenv("ENFORCE_ENTITLEMENTS", "").lower() in {"1", "true", "yes"}


def is_pro(user: Optional[User]) -> bool:
    return user is not None and user.plan == "pro"


def topic_is_free(topics: str) -> bool:
    """
    True when this game can be served from the bank.

    An empty topic is a whole-category game, which always means live
    generation — so it is not free. `match_bank_topic` is the same function
    the generator uses to decide, which is what keeps "what we charge for" and
    "what actually costs us" from drifting apart.
    """
    return match_bank_topic(topics) is not None


def may_create_game(user: Optional[User], topics: str) -> tuple[bool, str]:
    """
    (allowed, reason). Reason is empty when allowed.

    Anonymous hosts are not blocked — hosting without an account is the free
    tier, not a degraded state. What gates a game is the topic, not the login.
    """
    if not enforcement_on():
        return True, ""
    if is_pro(user):
        return True, ""
    if topic_is_free(topics):
        return True, ""
    return False, (
        "Custom topics need a Pro account. "
        "Pick one of the curated topics, or upgrade to play any title."
    )
