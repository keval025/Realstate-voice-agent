"""Talk to the agent from your browser, no phone number needed.

    python dev.py                    # then open http://localhost:7860/client
    python dev.py --client sunrise-realty

Uses the same prompt, tools, calendar and dashboard as real calls. Calls show
up in the dashboard with caller ID "+15550000000".
"""

import sys
import uuid

from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.transports.base_transport import TransportParams

from app.bot import run_call
from app.clients import ClientRegistry
from app.config import settings
from app.db import Database
from app.postcall import finalize_call
from app.session import CallSession

CLIENT_ID = None
for i, a in enumerate(sys.argv):
    if a == "--client" and i + 1 < len(sys.argv):
        CLIENT_ID = sys.argv[i + 1]
        del sys.argv[i : i + 2]
        break


async def bot(runner_args: RunnerArguments):
    registry = ClientRegistry(settings.clients_dir)
    client = registry.get(CLIENT_ID) if CLIENT_ID else (registry.all() or [None])[0]
    if client is None:
        raise SystemExit("No client profile found. Add one to the clients/ folder.")

    transport = await create_transport(
        runner_args,
        {"webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True)},
    )
    db = Database(settings.database_path)
    session = CallSession(
        call_sid=f"DEV-{uuid.uuid4().hex[:10]}",
        client=client,
        caller_number="+15550000000",
        called_number=client.phone_numbers[0] if client.phone_numbers else "",
        db=db,
        transfer_available=False,  # can't transfer a browser session to a phone
    )
    await db.call_started(session.call_sid, client.id, session.caller_number, session.called_number)
    try:
        await run_call(transport, session, telephony=False)
    finally:
        await finalize_call(session)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
