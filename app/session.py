"""Per-call state shared by the pipeline, the tools and the post-call step."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.clients import ClientProfile
from app.db import Database
from app.leads import Lead


@dataclass
class CallSession:
    call_sid: str
    client: ClientProfile
    caller_number: str
    called_number: str
    db: Database
    transfer_available: bool = False
    lead: Lead = field(default_factory=Lead)
    transcript: list[dict] = field(default_factory=list)
    started_monotonic: float = field(default_factory=time.monotonic)
    offered_slots: list[dict] = field(default_factory=list)
    appointment_at: str = ""
    booking_uid: str = ""
    transferred: bool = False
    handoff_pending: bool = False  # call is being moved to a human; don't hang it up
    end_reason: str = ""
    idle_prompts: int = 0

    def add_line(self, role: str, text: str) -> None:
        text = (text or "").strip()
        if text:
            self.transcript.append(
                {"t": round(time.monotonic() - self.started_monotonic, 1), "role": role, "text": text}
            )

    @property
    def duration_sec(self) -> int:
        return int(time.monotonic() - self.started_monotonic)
