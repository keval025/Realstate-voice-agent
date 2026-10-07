"""Small async wrappers around the Twilio REST API (no blocking SDK calls in the event loop)."""

from __future__ import annotations

from xml.sax.saxutils import escape

import aiohttp
from loguru import logger

from app.config import settings

API = "https://api.twilio.com/2010-04-01/Accounts/{sid}"


def _auth() -> aiohttp.BasicAuth:
    return aiohttp.BasicAuth(settings.twilio_account_sid, settings.twilio_auth_token)


async def _post(path: str, data: dict) -> dict | None:
    url = API.format(sid=settings.twilio_account_sid) + path
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            async with s.post(url, data=data, auth=_auth()) as r:
                body = await r.json(content_type=None)
                if r.status >= 400:
                    logger.error(f"Twilio {path} failed ({r.status}): {body}")
                    return None
                return body
    except Exception as e:  # network errors must never crash a call
        logger.error(f"Twilio {path} error: {e}")
        return None


async def send_sms(from_number: str, to_number: str, body: str) -> bool:
    res = await _post("/Messages.json", {"From": from_number, "To": to_number, "Body": body[:1500]})
    return res is not None


async def start_recording(call_sid: str) -> bool:
    data = {"RecordingChannels": "dual"}
    if settings.public_base_url:
        data["RecordingStatusCallback"] = f"{settings.public_base_url}/twilio/recording"
        data["RecordingStatusCallbackEvent"] = "completed"
    res = await _post(f"/Calls/{call_sid}/Recordings.json", data)
    return res is not None


async def fetch_media(url: str) -> bytes | None:
    if not url.startswith("https://api.twilio.com/"):
        return None
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            async with s.get(url, auth=_auth()) as r:
                return await r.read() if r.status == 200 else None
    except Exception as e:
        logger.error(f"Twilio media fetch error: {e}")
        return None


def transfer_twiml(number: str, caller_id: str, whisper: str = "") -> str:
    say = f"<Say>{escape(whisper)}</Say>" if whisper else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        f'{say}<Dial callerId="{escape(caller_id)}" timeout="25">{escape(number)}</Dial>'
        "<Say>Sorry, nobody was able to pick up. Someone from the team will call you back shortly. Goodbye.</Say>"
        "</Response>"
    )


def fallback_verbs(transfer_number: str, caller_id: str) -> str:
    """TwiML verbs used when the AI can't handle a call (busy, outage, crash)."""
    if transfer_number:
        return f'<Dial callerId="{escape(caller_id)}" timeout="25">{escape(transfer_number)}</Dial>'
    return (
        "<Say>Sorry, we can't take your call right now. Please leave your name, number and what you're "
        'looking for after the tone.</Say><Record maxLength="120" playBeep="true"/>'
    )


async def redirect_to_fallback(call_sid: str, transfer_number: str, caller_id: str) -> bool:
    body = '<?xml version="1.0" encoding="UTF-8"?><Response>' + fallback_verbs(transfer_number, caller_id) + "</Response>"
    return await _post(f"/Calls/{call_sid}.json", {"Twiml": body}) is not None


async def transfer_call(call_sid: str, number: str, caller_id: str) -> bool:
    """Redirect a live call to a human. This ends the media stream (and the bot)."""
    res = await _post(f"/Calls/{call_sid}.json", {"Twiml": transfer_twiml(number, caller_id)})
    return res is not None
