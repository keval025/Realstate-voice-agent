"""Functions the AI can call during the conversation.

Pipecat builds each tool's schema from the function signature and docstring,
so the docstrings below are what the model reads. Keep them precise.
"""

from __future__ import annotations

import asyncio
import re

from loguru import logger
from pipecat.frames.frames import EndWorkerFrame, FunctionCallResultProperties, TTSSpeakFrame
from pipecat.services.llm_service import FunctionCallParams

from app import calendar_calcom, twilio_api
from app.leads import score_lead
from app.session import CallSession

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")


def _session(params: FunctionCallParams) -> CallSession:
    return params.app_resources


async def save_lead_details(
    params: FunctionCallParams,
    name: str = "",
    intent: str = "",
    areas: str = "",
    property_address: str = "",
    budget: str = "",
    timeline: str = "",
    financing: str = "",
    property_type: str = "",
    bedrooms: str = "",
    has_agent: str = "",
    motivation: str = "",
    callback_number: str = "",
    email: str = "",
    notes: str = "",
):
    """Save details the caller just shared. Call this whenever you learn something new; pass only new fields.

    Args:
        name: Caller's name.
        intent: One of: buy, sell, buy_and_sell, rent, invest, other.
        areas: Cities or neighborhoods they're interested in.
        property_address: Address of the home they want to sell.
        budget: Buyer price range or seller's hoped-for price, as said, e.g. "400 to 450 thousand".
        timeline: When they want to move, as said, e.g. "within 2 months".
        financing: One of: cash, pre_approved, needs_lender, unknown.
        property_type: e.g. single family, condo, townhouse, land.
        bedrooms: Number of bedrooms wanted or in their home.
        has_agent: yes if already working with an agent, otherwise no.
        motivation: Why they're moving, e.g. relocating for work, growing family, downsizing.
        callback_number: Best phone number to reach them, digits only.
        email: Email address, only after spelling it back and confirming.
        notes: Anything else useful for the agent.
    """
    s = _session(params)
    args = dict(
        name=name, intent=intent, areas=areas, property_address=property_address, budget=budget,
        timeline=timeline, financing=financing, property_type=property_type, bedrooms=bedrooms,
        has_agent=has_agent, motivation=motivation, callback_number=callback_number, email=email, notes=notes,
    )
    if email and not EMAIL_RE.match(email.strip()):
        args["email"] = ""
        args["notes"] = (notes + f" (unconfirmed email heard as: {email})").strip()
    changed = s.lead.merge(**args)
    sc = score_lead(s.lead)
    try:
        await s.db.update_call(s.call_sid, lead_json=s.lead.to_dict(), score=sc.points, tier=sc.tier)
    except Exception as e:
        logger.error(f"save_lead_details db error: {e}")
    await params.result_callback(
        {
            "saved": changed,
            "still_missing": s.lead.missing_for_qualification(),
            "lead_tier": sc.tier,
        }
    )


async def check_availability(params: FunctionCallParams):
    """Get the next open appointment times with the team. Read at most three options to the caller."""
    s = _session(params)
    if not s.client.calendar_enabled:
        await params.result_callback({"available": False, "say": "Booking isn't available; offer a callback."})
        return
    await params.llm.push_frame(TTSSpeakFrame("One moment while I check the calendar.", append_to_context=False))
    try:
        slots = await calendar_calcom.get_open_slots(s.client)
    except Exception as e:
        logger.error(f"check_availability failed: {e}")
        await params.result_callback(
            {"available": False, "error": "calendar unavailable", "say": "Offer a callback instead."}
        )
        return
    s.offered_slots = slots
    if not slots:
        await params.result_callback({"available": False, "say": "No open times this week; offer a callback."})
        return
    await params.result_callback(
        {
            "available": True,
            "slots": [{"slot_id": i + 1, "time": sl["spoken"]} for i, sl in enumerate(slots)],
        }
    )


async def book_appointment(params: FunctionCallParams, slot_id: int, email: str = ""):
    """Book the time slot the caller chose. Only call after check_availability and after the caller confirms.

    Args:
        slot_id: The slot_id number from check_availability that the caller picked.
        email: Caller's confirmed email address for the calendar invite. Empty if they declined to give one.
    """
    s = _session(params)
    if not s.offered_slots:
        await params.result_callback({"booked": False, "error": "Call check_availability first."})
        return
    if not 1 <= int(slot_id) <= len(s.offered_slots):
        await params.result_callback({"booked": False, "error": "Invalid slot_id."})
        return
    slot = s.offered_slots[int(slot_id) - 1]
    email = (email or s.lead.email).strip()
    if email and EMAIL_RE.match(email):
        s.lead.merge(email=email)
    else:
        # Cal.com requires an email; use a non-deliverable placeholder so booking still works.
        digits = re.sub(r"\D", "", s.lead.callback_number or s.caller_number) or "caller"
        email = f"{digits}@no-email.invalid"
    await params.llm.push_frame(TTSSpeakFrame("Great, booking that now.", append_to_context=False))
    try:
        res = await calendar_calcom.book(
            s.client,
            slot["start"],
            name=s.lead.name,
            email=email,
            phone=s.lead.callback_number or s.caller_number,
            notes={
                "source": "AI phone assistant",
                "call_sid": s.call_sid,
                "intent": s.lead.intent,
                "areas": s.lead.areas or s.lead.property_address,
                "budget": s.lead.budget,
                "timeline": s.lead.timeline,
                "financing": s.lead.financing,
            },
        )
    except Exception as e:
        logger.error(f"book_appointment failed: {e}")
        await params.result_callback(
            {"booked": False, "say": "That time was just taken or booking failed. Offer another slot or a callback."}
        )
        return
    s.appointment_at, s.booking_uid = slot["start"], res.get("uid", "")
    await s.db.update_call(s.call_sid, appointment_at=s.appointment_at, booking_uid=s.booking_uid)
    await params.result_callback({"booked": True, "time": slot["spoken"]})


async def transfer_to_agent(params: FunctionCallParams, reason: str):
    """Transfer the caller to a live team member. Save lead details first and tell the caller you're connecting them.

    Args:
        reason: Short reason for the transfer, e.g. "hot buyer wants to talk now".
    """
    s = _session(params)
    if not (s.transfer_available and s.client.transfer_number):
        await params.result_callback({"transferred": False, "say": "No one is available; offer booking or callback."})
        return
    s.lead.merge(notes=f"transfer requested: {reason}")
    await params.llm.push_frame(
        TTSSpeakFrame("Perfect, I'm connecting you now. Please hold for just a moment.", append_to_context=False)
    )
    await asyncio.sleep(2.5)  # let the "connecting you" line finish playing
    s.handoff_pending = True
    ok = await twilio_api.transfer_call(
        s.call_sid, s.client.transfer_number, s.client.phone_numbers[0] if s.client.phone_numbers else s.called_number
    )
    if ok:
        s.transferred = True
        s.end_reason = "transferred"
        await params.result_callback({"transferred": True}, properties=_no_llm())
    else:
        s.handoff_pending = False
        await params.result_callback({"transferred": False, "say": "Transfer failed; apologize and offer a callback."})


async def end_call(params: FunctionCallParams, reason: str = "completed"):
    """Hang up. Only call this AFTER you have said goodbye to the caller.

    Args:
        reason: completed, not_interested, wrong_number, spam, or has_agent.
    """
    s = _session(params)
    s.end_reason = s.end_reason or reason
    await params.result_callback({"ending": True}, properties=_no_llm())
    await params.llm.push_frame(EndWorkerFrame(reason=reason))


def _no_llm() -> FunctionCallResultProperties:
    return FunctionCallResultProperties(run_llm=False)


ALL_TOOLS = [save_lead_details, check_availability, book_appointment, transfer_to_agent, end_call]
