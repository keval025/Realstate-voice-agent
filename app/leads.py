"""Lead data captured during a call, and the scoring rules that rank it.

Scoring is deterministic on purpose: clients can see exactly why a lead is "hot",
and you can tune the weights per client without touching the AI.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

LEAD_FIELDS = (
    "name",
    "email",
    "callback_number",
    "intent",
    "areas",
    "property_address",
    "budget",
    "timeline",
    "financing",
    "property_type",
    "bedrooms",
    "has_agent",
    "motivation",
    "notes",
)


@dataclass
class Lead:
    name: str = ""
    email: str = ""
    callback_number: str = ""
    intent: str = ""  # buy | sell | buy_and_sell | rent | invest | other
    areas: str = ""
    property_address: str = ""  # for sellers
    budget: str = ""  # buyers' price range, or sellers' expected price
    timeline: str = ""  # free text as the caller said it, e.g. "within 2 months"
    financing: str = ""  # cash | pre_approved | needs_lender | unknown
    property_type: str = ""
    bedrooms: str = ""
    has_agent: str = ""  # yes | no | unknown
    motivation: str = ""
    notes: str = ""

    def merge(self, **updates) -> list[str]:
        """Merge non-empty values. Returns the names of fields that changed."""
        changed = []
        for key, value in updates.items():
            if key not in LEAD_FIELDS or value is None:
                continue
            value = str(value).strip()
            if not value:
                continue
            if key == "notes" and self.notes and value not in self.notes:
                value = f"{self.notes}; {value}"
            if getattr(self, key) != value:
                setattr(self, key, value)
                changed.append(key)
        return changed

    def to_dict(self) -> dict:
        return asdict(self)

    def missing_for_qualification(self) -> list[str]:
        needed = ["name", "intent", "timeline"]
        if self.intent in ("buy", "buy_and_sell", "invest", "rent"):
            needed += ["areas", "budget", "financing"]
        if self.intent in ("sell", "buy_and_sell"):
            needed += ["property_address"]
        needed.append("has_agent")
        return [f for f in needed if not getattr(self, f)]


# ----------------------------------------------------------------------------
# Scoring
# ----------------------------------------------------------------------------

_WORD_NUM = {
    "a": 1, "one": 1, "two": 2, "couple": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12,
}


def timeline_months(text: str) -> float | None:
    """Rough conversion of a spoken timeline into months. None if unknown."""
    t = (text or "").lower()
    if not t:
        return None
    if re.search(r"\b(just looking|browsing|not sure|don'?t know|someday|no rush|not now)\b", t):
        return 18
    if re.search(r"\b(asap|right away|immediately|right now|now|this week|urgent)\b", t):
        return 0.25
    if "next week" in t or "few weeks" in t or "couple weeks" in t or "couple of weeks" in t:
        return 0.5
    if "this month" in t or "next month" in t:
        return 1
    m =re.search(r"(\d+(?:\.\d+)?)\s*(?:-|to)?\s*(\d+)?\s*(day|week|month|year)", t)
    if not m:
        m2 = re.search(r"\b(a|one|two|couple|three|four|five|six|seven|eight|nine|ten|twelve)\b\s*(?:of\s*)?(day|week|month|year)", t)
        if not m2:
            if "spring" in t or "summer" in t or "fall" in t or "winter" in t:
                return 5
            if "next year" in t:
                return 12
            return None
        n, unit = _WORD_NUM[m2.group(1)], m2.group(2)
    else:
        n = float(m.group(2) or m.group(1))
        unit = m.group(3)
    factor = {"day": 1 / 30, "week": 0.25, "month": 1, "year": 12}[unit]
    return n * factor


@dataclass
class Score:
    points: int
    tier: str  # hot | warm | cold | disqualified
    reasons: list[str] = field(default_factory=list)


def score_lead(lead: Lead) -> Score:
    pts, reasons = 0, []

    months = timeline_months(lead.timeline)
    if months is not None:
        if months <= 1:
            pts += 35; reasons.append("moving within a month")
        elif months <= 3:
            pts += 28; reasons.append("moving within 3 months")
        elif months <= 6:
            pts += 15; reasons.append("moving within 6 months")
        elif months <= 12:
            pts += 6; reasons.append("moving within a year")
        else:
            reasons.append("long timeline")

    fin = (lead.financing or "").lower()
    if lead.intent in ("sell",):
        pts += 20; reasons.append("seller (listing opportunity)")
    elif "cash" in fin:
        pts += 25; reasons.append("cash buyer")
    elif "pre" in fin and "approv" in fin:
        pts += 22; reasons.append("pre-approved")
    elif "need" in fin or "lender" in fin:
        pts += 8; reasons.append("needs a lender")

    if lead.intent == "buy_and_sell":
        pts += 10; reasons.append("buying and selling (two transactions)")

    if lead.budget:
        pts += 8
    if lead.areas or lead.property_address:
        pts += 7
    if lead.motivation:
        pts += 7; reasons.append(f"motivation: {lead.motivation}")
    if lead.name and (lead.callback_number or lead.email):
        pts += 8

    has_agent = (lead.has_agent or "").lower()
    if has_agent.startswith("y"):
        return Score(points=min(pts, 100), tier="disqualified", reasons=reasons + ["already working with an agent"])
    if lead.intent == "other":
        return Score(points=min(pts, 100), tier="disqualified", reasons=reasons + ["not a buyer or seller"])

    pts = min(pts, 100)
    tier = "hot" if pts >= 65 else "warm" if pts >= 40 else "cold"
    return Score(points=pts, tier=tier, reasons=reasons)


TIER_RANK = {"disqualified": 0, "cold": 1, "warm": 2, "hot": 3}
