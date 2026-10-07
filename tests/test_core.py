import asyncio
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("APP_SECRET", "test-secret-test-secret-123456")

from app.calendar_calcom import speak_slot  # noqa: E402
from app.clients import ClientProfile, ClientRegistry, hash_password, normalize_phone, verify_password  # noqa: E402
from app.db import Database  # noqa: E402
from app.leads import Lead, score_lead, timeline_months  # noqa: E402
from app.prompts import greeting, system_prompt  # noqa: E402


# ---------------- phone + clients ----------------
def test_normalize_phone():
    assert normalize_phone("(512) 555-0123") == "+15125550123"
    assert normalize_phone("+1 512 555 0123") == "+15125550123"
    assert normalize_phone("15125550123") == "+15125550123"
    assert normalize_phone("") == ""


def test_registry_loads_example():
    reg = ClientRegistry("clients")
    c = reg.by_number("512-555-0123")
    assert c is not None and c.id == "sunrise-realty"
    assert c.calendar_enabled


def test_passwords():
    h = hash_password("hunter2")
    assert verify_password("hunter2", h)
    assert not verify_password("wrong", h)


def test_business_hours():
    c = ClientProfile(id="x", business_name="X", timezone="America/Chicago",
                      business_hours={"mon": "09:00-18:00", "sun": None})
    tz = ZoneInfo("America/Chicago")
    assert c.is_open(datetime(2026, 10, 5, 10, 0, tzinfo=tz))       # Monday 10am
    assert not c.is_open(datetime(2026, 10, 5, 19, 0, tzinfo=tz))   # Monday 7pm
    assert not c.is_open(datetime(2026, 10, 4, 10, 0, tzinfo=tz))   # Sunday


# ---------------- scoring ----------------
@pytest.mark.parametrize("text,expected", [
    ("asap", 0.25), ("right now", 0.25), ("I don't know yet", 18), ("within 2 months", 2),
    ("in a couple of months", 2), ("6 months", 6), ("next year", 12), ("just looking", 18),
    ("3 to 4 weeks", 1.0), ("", None),
])
def test_timeline(text, expected):
    assert timeline_months(text) == expected


def test_hot_buyer():
    lead = Lead(name="Sam", callback_number="+15125550000", intent="buy", areas="Austin",
                budget="450k", timeline="within 2 months", financing="pre_approved", has_agent="no",
                motivation="relocating for work")
    s = score_lead(lead)
    assert s.tier == "hot", s


def test_cold_browser():
    s = score_lead(Lead(intent="buy", timeline="just looking", financing="unknown"))
    assert s.tier == "cold"


def test_has_agent_disqualifies():
    s = score_lead(Lead(intent="buy", timeline="asap", financing="cash", has_agent="yes"))
    assert s.tier == "disqualified"


def test_merge_and_missing():
    lead = Lead()
    assert lead.merge(name="Sam", intent="sell", bogus="x") == ["name", "intent"]
    assert "property_address" in lead.missing_for_qualification()
    lead.merge(notes="a"); lead.merge(notes="b")
    assert lead.notes == "a; b"


# ---------------- prompt ----------------
def test_prompt_contains_rules():
    c = ClientRegistry("clients").get("sunrise-realty")
    p = system_prompt(c, "+15125550000", transfer_available=False)
    assert "Fair Housing" in p and "NOT available" in p and "Round Rock" in p
    assert "virtual assistant" in greeting(c) and "recorded" in greeting(c)


def test_speak_slot():
    tz = ZoneInfo("America/Chicago")
    now = datetime(2026, 10, 7, 9, 0, tzinfo=tz)
    assert speak_slot(datetime(2026, 10, 7, 14, 0, tzinfo=tz), now) == "today at 2 PM"
    assert speak_slot(datetime(2026, 10, 8, 10, 30, tzinfo=tz), now) == "tomorrow at 10:30 AM"
    assert speak_slot(datetime(2026, 10, 9, 9, 0, tzinfo=tz), now) == "Friday, October 9th at 9 AM"


# ---------------- database ----------------
def test_db_roundtrip(tmp_path):
    async def go():
        db = Database(str(tmp_path / "t.db"))
        await db.call_started("CA1", "c1", "+1", "+2")
        await db.update_call("CA1", tier="hot", score=80, lead_json={"name": "Sam"}, booking_uid="b1")
        row = await db.get_call("CA1")
        assert row["lead_json"]["name"] == "Sam"
        st = await db.stats("c1")
        assert st["hot"] == 1 and st["booked"] == 1 and st["total"] == 1
    asyncio.run(go())
