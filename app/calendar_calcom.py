"""Cal.com v2 integration: find open slots and book an appointment.

Each client connects their own Cal.com account (free plan works). Put the event
type id in their YAML and their API key in an env var.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import aiohttp
from loguru import logger

from app.clients import ClientProfile
from app.config import settings


class CalendarError(Exception):
    pass


def _headers(api_key: str, version: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "cal-api-version": version,
        "Content-Type": "application/json",
    }


def speak_slot(dt: datetime, now: datetime) -> str:
    """'tomorrow at 2 PM', 'Thursday, October 9th at 10:30 AM'."""
    day_delta = (dt.date() - now.date()).days
    minute = "" if dt.minute == 0 else f":{dt.minute:02d}"
    hour = dt.hour % 12 or 12
    clock = f"{hour}{minute} {'AM' if dt.hour < 12 else 'PM'}"
    if day_delta == 0:
        return f"today at {clock}"
    if day_delta == 1:
        return f"tomorrow at {clock}"
    d = dt.day
    suffix = "th" if 11 <= d <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(d % 10, "th")
    return f"{dt.strftime('%A, %B')} {d}{suffix} at {clock}"


async def get_open_slots(client: ClientProfile, days_ahead: int = 5, limit: int = 6) -> list[dict]:
    """Return up to ``limit`` upcoming slots as {"start": iso_utc, "spoken": str}."""
    api_key = client.calendar_api_key
    if not api_key:
        raise CalendarError("Calendar API key not configured")
    tz = ZoneInfo(client.timezone)
    now = datetime.now(tz)
    start = (now + timedelta(hours=int(client.calendar.get("min_notice_hours", 2)))).astimezone(timezone.utc)
    end = (now + timedelta(days=days_ahead)).astimezone(timezone.utc)
    params = {
        "eventTypeId": str(client.calendar["event_type_id"]),
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timeZone": client.timezone,
    }
    url = f"{settings.calcom_base_url}/slots"
    timeout = aiohttp.ClientTimeout(total=8)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.get(url, params=params, headers=_headers(api_key, settings.calcom_slots_version)) as r:
            body = await r.json(content_type=None)
            if r.status >= 400 or body.get("status") != "success":
                logger.error(f"Cal.com slots error {r.status}: {body}")
                raise CalendarError("Could not load availability")

    slots: list[datetime] = []
    for _day, items in sorted((body.get("data") or {}).items()):
        for item in items:
            raw = item.get("start") if isinstance(item, dict) else item
            try:
                slots.append(datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(tz))
            except (AttributeError, ValueError):
                continue
    slots.sort()
    # Spread choices: at most 2 per day, so the caller hears different days.
    picked, per_day = [], {}
    for dt in slots:
        if per_day.get(dt.date(), 0) >= 2:
            continue
        # skip slots less than 90 minutes after the previous pick on the same day
        if picked and picked[-1].date() == dt.date() and (dt - picked[-1]) < timedelta(minutes=90):
            continue
        picked.append(dt)
        per_day[dt.date()] = per_day.get(dt.date(), 0) + 1
        if len(picked) >= limit:
            break
    return [
        {"start": dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "spoken": speak_slot(dt, now)}
        for dt in picked
    ]


async def book(
    client: ClientProfile,
    start_utc: str,
    name: str,
    email: str,
    phone: str,
    notes: dict,
) -> dict:
    """Create a booking. Returns {"uid": str, "start": iso}."""
    api_key = client.calendar_api_key
    if not api_key:
        raise CalendarError("Calendar API key not configured")
    attendee = {"name": name or "Caller", "email": email, "timeZone": client.timezone, "language": "en"}
    if phone:
        attendee["phoneNumber"] = phone
    payload = {
        "start": start_utc,
        "eventTypeId": int(client.calendar["event_type_id"]),
        "attendee": attendee,
        # Cal.com metadata: max 50 keys, values max 500 chars
        "metadata": {k[:40]: str(v)[:500] for k, v in list(notes.items())[:50] if v},
    }
    url = f"{settings.calcom_base_url}/bookings"
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.post(url, json=payload, headers=_headers(api_key, settings.calcom_bookings_version)) as r:
            body = await r.json(content_type=None)
            if r.status >= 400 or body.get("status") != "success":
                logger.error(f"Cal.com booking error {r.status}: {body}")
                raise CalendarError("Booking failed")
    data = body.get("data") or {}
    if isinstance(data, list):
        data = data[0] if data else {}
    return {"uid": data.get("uid", ""), "start": data.get("start", start_utc)}
