"""Tool handlers tested with a fake FunctionCallParams (no network)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from app.clients import ClientRegistry
from app.db import Database
from app.session import CallSession
from app import tools


class FakeLLM:
    def __init__(self):
        self.frames = []

    async def push_frame(self, frame, *a, **k):
        self.frames.append(frame)


def make(tmp_path, transfer=False):
    db = Database(str(tmp_path / "t.db"))
    client = ClientRegistry("clients").get("sunrise-realty")
    s = CallSession(call_sid="CA1", client=client, caller_number="+15125550000",
                    called_number="+15125550123", db=db, transfer_available=transfer)
    asyncio.run(db.call_started("CA1", client.id, s.caller_number, s.called_number))
    results = []

    async def cb(result, properties=None):
        results.append((result, properties))

    params = SimpleNamespace(app_resources=s, result_callback=cb, llm=FakeLLM())
    return s, params, results


def test_save_lead_details(tmp_path):
    s, p, r = make(tmp_path)
    asyncio.run(tools.save_lead_details(p, name="Sam", intent="buy", timeline="asap", financing="cash",
                                        email="not an email"))
    assert s.lead.name == "Sam" and s.lead.email == ""
    assert "unconfirmed email" in s.lead.notes
    assert r[-1][0]["lead_tier"] in ("warm", "hot")
    row = asyncio.run(s.db.get_call("CA1"))
    assert row["lead_json"]["name"] == "Sam"


def test_booking_flow(tmp_path):
    s, p, r = make(tmp_path)

    async def fake_slots(client):
        return [{"start": "2026-10-08T15:00:00Z", "spoken": "tomorrow at 10 AM"}]

    async def fake_book(client, start, **kw):
        assert kw["email"].endswith("@no-email.invalid")
        return {"uid": "bk_1", "start": start}

    with patch("app.calendar_calcom.get_open_slots", fake_slots), patch("app.calendar_calcom.book", fake_book):
        asyncio.run(tools.book_appointment(p, slot_id=1))
        assert r[-1][0]["booked"] is False  # must check availability first
        asyncio.run(tools.check_availability(p))
        assert r[-1][0]["slots"][0]["slot_id"] == 1
        asyncio.run(tools.book_appointment(p, slot_id=5))
        assert r[-1][0]["booked"] is False
        asyncio.run(tools.book_appointment(p, slot_id=1))
        assert r[-1][0] == {"booked": True, "time": "tomorrow at 10 AM"}
    assert s.booking_uid == "bk_1"


def test_transfer_blocked_after_hours(tmp_path):
    s, p, r = make(tmp_path, transfer=False)
    asyncio.run(tools.transfer_to_agent(p, reason="wants a human"))
    assert r[-1][0]["transferred"] is False and not s.transferred


def test_transfer_ok(tmp_path):
    s, p, r = make(tmp_path, transfer=True)

    async def fake_transfer(*a):
        return True

    with patch("app.twilio_api.transfer_call", fake_transfer):
        asyncio.run(tools.transfer_to_agent(p, reason="hot buyer"))
    assert s.transferred and r[-1][1].run_llm is False


def test_end_call_pushes_end_frame(tmp_path):
    from pipecat.frames.frames import EndWorkerFrame

    s, p, r = make(tmp_path)
    asyncio.run(tools.end_call(p, reason="wrong_number"))
    assert isinstance(p.llm.frames[-1], EndWorkerFrame) and s.end_reason == "wrong_number"


def test_serializer_skips_hangup_on_handoff(tmp_path):
    from app.serializer import HandoffAwareTwilioSerializer

    s, _, _ = make(tmp_path)
    ser = HandoffAwareTwilioSerializer(session=s, stream_sid="MZ", call_sid="CA1", account_sid="AC", auth_token="t")
    called = []

    async def fake_super(self):
        called.append(1)

    with patch("pipecat.serializers.twilio.TwilioFrameSerializer._hang_up_call", fake_super):
        s.handoff_pending = True
        asyncio.run(ser._hang_up_call())
        assert called == []
        s.handoff_pending = False
        asyncio.run(ser._hang_up_call())
        assert called == [1]
