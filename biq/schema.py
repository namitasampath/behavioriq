"""Canonical contracts for BehaviorIQ. This is the ONLY place the event schema is defined.

Every other module imports from here. See docs/CONTRACTS.md for the prose version.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

EVENT_TYPES = [
    # passive / navigational
    "page_view", "search", "click", "scroll", "hover",
    # product consideration
    "product_view", "review_view", "compare",
    # transaction funnel
    "add_to_cart", "remove_from_cart", "checkout_start", "purchase",
    # termination
    "exit",
]
EventType = Literal[
    "page_view", "search", "click", "scroll", "hover",
    "product_view", "review_view", "compare",
    "add_to_cart", "remove_from_cart", "checkout_start", "purchase", "exit",
]

TERMINAL_EVENTS = {"purchase", "exit"}
SESSION_TIMEOUT_S = 30 * 60  # 30 minutes of inactivity closes a session
OUTCOMES = ["purchase", "abandon", "browse"]
DEVICES = ["desktop", "mobile", "tablet"]
REFERRERS = ["direct", "search", "social", "email", "ads"]


class EventMeta(BaseModel):
    scroll_depth: float = Field(0.0, ge=0.0, le=1.0)
    dwell_ms: int = Field(0, ge=0)
    seq: int = Field(0, ge=0, description="Per-session sequence number, used to verify ordering")


class Event(BaseModel):
    event_id: str
    session_id: str
    anon_user_id: str
    event_type: EventType
    timestamp: datetime
    page: str
    product_id: Optional[str] = None
    category: Optional[str] = None
    device: Literal["desktop", "mobile", "tablet"]
    referrer: str
    meta: EventMeta = EventMeta()


def label_session(event_types: list[str]) -> str:
    """Outcome rule (fixed): purchase if any purchase; abandon if add_to_cart but no purchase; else browse."""
    if "purchase" in event_types:
        return "purchase"
    if "add_to_cart" in event_types:
        return "abandon"
    return "browse"


def prediction_prefix(event_types: list[str]) -> int:
    """Number of events usable for intent prediction: everything before the first terminal event.

    Prevents label leakage (the token 'purchase' must never be an input).
    """
    for i, e in enumerate(event_types):
        if e in TERMINAL_EVENTS:
            return max(i, 1)
    return len(event_types)
