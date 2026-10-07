"""Client (brokerage) profiles.

Each client you sell to gets one YAML file in ``clients/``. The agent picks the
profile by the Twilio number that was dialled, so one server can run many clients.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def normalize_phone(number: str | None) -> str:
    """Return a US number in E.164 form (+1XXXXXXXXXX) where possible."""
    if not number:
        return ""
    digits = re.sub(r"\D", "", number)
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return "+" + digits if digits else ""


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or os.urandom(8).hex()
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return f"pbkdf2${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, _ = stored.split("$", 2)
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, salt), stored)


@dataclass
class ClientProfile:
    id: str
    business_name: str
    agent_name: str = "Ava"
    team_member_name: str = "one of our agents"
    phone_numbers: list[str] = field(default_factory=list)
    timezone: str = "America/New_York"
    service_areas: list[str] = field(default_factory=list)
    business_hours: dict[str, str | None] = field(default_factory=dict)
    transfer_number: str = ""
    alert_sms_numbers: list[str] = field(default_factory=list)
    alert_min_tier: str = "warm"  # hot | warm | cold
    record_calls: bool = True
    voice_id: str = ""
    calendar: dict = field(default_factory=dict)
    crm_webhook_url: str = ""
    knowledge: str = ""
    dashboard_username: str = ""
    dashboard_password_hash: str = ""

    # ---------- helpers ----------
    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def is_open(self, at: datetime | None = None) -> bool:
        """True when a human is available to take a live transfer."""
        at = (at or self.now()).astimezone(self.tz)
        span = self.business_hours.get(DAYS[at.weekday()])
        if not span:
            return False
        try:
            start_s, end_s = [s.strip() for s in span.split("-")]
            start = time.fromisoformat(start_s)
            end = time.fromisoformat(end_s)
        except ValueError:
            return False
        return start <= at.time() < end

    def hours_text(self) -> str:
        parts = []
        for d in DAYS:
            span = self.business_hours.get(d)
            parts.append(f"{d.title()}: {span if span else 'closed'}")
        return ", ".join(parts)

    @property
    def calendar_enabled(self) -> bool:
        return bool(self.calendar.get("provider") == "calcom" and self.calendar.get("event_type_id"))

    @property
    def calendar_api_key(self) -> str:
        env_name = self.calendar.get("api_key_env", "")
        return os.getenv(env_name, "") if env_name else ""

    @property
    def appointment_label(self) -> str:
        return self.calendar.get("appointment_label", "a quick call with an agent")


class ClientRegistry:
    def __init__(self, directory: str):
        self.directory = Path(directory)
        self._by_id: dict[str, ClientProfile] = {}
        self._by_number: dict[str, ClientProfile] = {}
        self.reload()

    def reload(self) -> None:
        by_id: dict[str, ClientProfile] = {}
        by_number: dict[str, ClientProfile] = {}
        if self.directory.exists():
            for path in sorted(self.directory.glob("*.y*ml")):
                if path.name.startswith("_"):
                    continue
                data = yaml.safe_load(path.read_text()) or {}
                profile = ClientProfile(**data)
                if profile.id in by_id:
                    raise ValueError(f"Duplicate client id '{profile.id}' in {path}")
                profile.phone_numbers = [normalize_phone(n) for n in profile.phone_numbers]
                profile.transfer_number = normalize_phone(profile.transfer_number)
                profile.alert_sms_numbers = [normalize_phone(n) for n in profile.alert_sms_numbers]
                by_id[profile.id] = profile
                for n in profile.phone_numbers:
                    if n in by_number:
                        raise ValueError(f"Phone number {n} is assigned to two clients")
                    by_number[n] = profile
        self._by_id, self._by_number = by_id, by_number

    def by_number(self, number: str | None) -> ClientProfile | None:
        return self._by_number.get(normalize_phone(number))

    def get(self, client_id: str) -> ClientProfile | None:
        return self._by_id.get(client_id)

    def all(self) -> list[ClientProfile]:
        return list(self._by_id.values())

    def authenticate(self, username: str, password: str) -> ClientProfile | None:
        for c in self._by_id.values():
            if (
                c.dashboard_username
                and hmac.compare_digest(c.dashboard_username, username)
                and c.dashboard_password_hash
                and verify_password(password, c.dashboard_password_hash)
            ):
                return c
        return None
