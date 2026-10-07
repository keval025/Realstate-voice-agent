"""Everything that happens after the caller hangs up.

1. Ask Claude to read the full transcript, fill in any lead fields the live
   agent missed, and write a 2-3 sentence summary for the human agent.
2. Re-score the lead, save it, push it to the CRM and text the agent.

This runs in the background so it never delays the call itself.
"""

from __future__ import annotations

from anthropic import AsyncAnthropic
from loguru import logger

from app.config import settings
from app.db import utcnow
from app.leads import LEAD_FIELDS, score_lead
from app.notify import send_alerts, send_crm_webhook
from app.session import CallSession

EXTRACT_TOOL = {
    "name": "record_call_outcome",
    "description": "Record the structured outcome of a real estate lead call.",
    "input_schema": {
        "type": "object",
        "properties": {
            **{f: {"type": "string"} for f in LEAD_FIELDS},
            "summary": {
                "type": "string",
                "description": "2-3 sentence summary for the real estate agent: who, what they want, urgency, next step.",
            },
            "next_step": {
                "type": "string",
                "description": "One short action for the agent, e.g. 'Call back Tuesday after 5pm with condo listings under 400k'.",
            },
        },
        "required": ["summary", "next_step"],
    },
}


def transcript_text(session: CallSession) -> str:
    return "\n".join(f"{'Caller' if l['role'] == 'user' else 'Assistant'}: {l['text']}" for l in session.transcript)


async def extract(session: CallSession) -> dict:
    if not session.transcript or not settings.anthropic_api_key:
        return {}
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    prompt = (
        "Here is a phone call between a real estate team's AI receptionist and a caller.\n"
        f"Lead details already captured during the call: {session.lead.to_dict()}\n\n"
        "Fill in any fields that the transcript supports but are still empty, correct any that the transcript "
        "contradicts, and leave a field empty if it was never discussed. Use intent values: buy, sell, "
        "buy_and_sell, rent, invest, other. financing values: cash, pre_approved, needs_lender, unknown. "
        "has_agent: yes or no.\n\n"
        f"<transcript>\n{transcript_text(session)}\n</transcript>"
    )
    try:
        resp = await client.messages.create(
            model=settings.summary_model,
            max_tokens=1024,
            tools=[EXTRACT_TOOL],
            tool_choice={"type": "tool", "name": "record_call_outcome"},
            messages=[{"role": "user", "content": prompt}],
        )
        for block in resp.content:
            if block.type == "tool_use":
                return dict(block.input)
    except Exception as e:
        logger.error(f"Post-call extraction failed for {session.call_sid}: {e}")
    return {}


async def finalize_call(session: CallSession) -> dict:
    """Run once per call, after the media stream has closed."""
    out = await extract(session)
    # Merge AI extraction: never overwrite values the live agent saved, except empty ones.
    for f in LEAD_FIELDS:
        if out.get(f) and not getattr(session.lead, f):
            session.lead.merge(**{f: out[f]})
    if not session.lead.callback_number:
        session.lead.callback_number = session.caller_number

    sc = score_lead(session.lead)
    status = "transferred" if session.transferred else "completed"
    record = {
        "call_sid": session.call_sid,
        "client_id": session.client.id,
        "client_name": session.client.business_name,
        "caller_number": session.caller_number,
        "lead": session.lead.to_dict(),
        "score": sc.points,
        "tier": sc.tier,
        "score_reasons": sc.reasons,
        "summary": out.get("summary", ""),
        "next_step": out.get("next_step", ""),
        "appointment_at": session.appointment_at or None,
        "booking_uid": session.booking_uid or None,
        "transferred": session.transferred,
        "duration_sec": session.duration_sec,
        "ended_at": utcnow(),
        "transcript": session.transcript,
    }
    await session.db.update_call(
        session.call_sid,
        ended_at=record["ended_at"],
        duration_sec=session.duration_sec,
        status=status,
        end_reason=session.end_reason or "caller_hung_up",
        lead_json=record["lead"],
        score=sc.points,
        tier=sc.tier,
        score_reasons=sc.reasons,
        summary=record["summary"],
        next_step=record["next_step"],
        transferred=int(session.transferred),
        transcript_json=session.transcript,
    )

    # Skip CRM/alerts for empty calls (hang-ups during the greeting, robocalls).
    if not any(l["role"] == "user" for l in session.transcript):
        await session.db.update_call(session.call_sid, crm_status="skipped_empty", alert_status="skipped_empty")
        return record

    crm = await send_crm_webhook(session.client, record)
    alert = await send_alerts(session.client, record)
    await session.db.update_call(session.call_sid, crm_status=crm, alert_status=alert)
    logger.info(f"Call {session.call_sid} finalized: tier={sc.tier} score={sc.points} crm={crm} alert={alert}")
    return record
