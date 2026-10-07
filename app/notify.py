"""After-call delivery: CRM webhook + SMS alert to the client's agents."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json

import aiohttp
from loguru import logger

from app.clients import ClientProfile
from app.config import settings
from app.leads import TIER_RANK
from app.twilio_api import send_sms


def sign(payload: bytes) -> str:
    return hmac.new(settings.app_secret.encode(), payload, hashlib.sha256).hexdigest()


async def send_crm_webhook(client: ClientProfile, record: dict, retries: int = 3) -> str:
    """POST the lead to the client's CRM / Zapier / Make / GoHighLevel webhook."""
    if not client.crm_webhook_url:
        return "skipped"
    body = json.dumps(record, default=str).encode()
    headers = {"Content-Type": "application/json", "X-Signature-SHA256": sign(body)}
    for attempt in range(1, retries + 1):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
                async with s.post(client.crm_webhook_url, data=body, headers=headers) as r:
                    if r.status < 300:
                        return "sent"
                    logger.warning(f"CRM webhook attempt {attempt} got {r.status}")
        except Exception as e:
            logger.warning(f"CRM webhook attempt {attempt} error: {e}")
        await asyncio.sleep(2**attempt)
    return "failed"


def alert_text(client: ClientProfile, record: dict) -> str:
    lead = record.get("lead", {})
    tier = (record.get("tier") or "").upper()
    lines = [
        f"{tier} LEAD ({record.get('score')}/100) - {client.business_name}",
        f"{lead.get('name') or 'Unknown'} {record.get('caller_number') or ''}",
        f"Wants: {lead.get('intent') or '?'} | {lead.get('areas') or lead.get('property_address') or ''}",
        f"Budget: {lead.get('budget') or '?'} | Timeline: {lead.get('timeline') or '?'} | {lead.get('financing') or ''}",
    ]
    if record.get("appointment_at"):
        lines.append(f"BOOKED: {record['appointment_at']}")
    if record.get("summary"):
        lines.append(record["summary"][:400])
    if settings.public_base_url:
        lines.append(f"{settings.public_base_url}/dashboard/call/{record.get('call_sid')}")
    return "\n".join(lines)


async def send_alerts(client: ClientProfile, record: dict) -> str:
    if not client.alert_sms_numbers or not client.phone_numbers:
        return "skipped"
    tier = record.get("tier") or "cold"
    if TIER_RANK.get(tier, 0) < TIER_RANK.get(client.alert_min_tier, 2) and not record.get("appointment_at"):
        return "below_threshold"
    text = alert_text(client, record)
    ok = True
    for n in client.alert_sms_numbers:
        ok = await send_sms(client.phone_numbers[0], n, text) and ok
    return "sent" if ok else "failed"
