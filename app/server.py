"""HTTP + WebSocket server.

Routes
  POST /twilio/voice       Twilio "A call comes in" webhook -> returns TwiML that opens a media stream
  WS   /twilio/ws          Twilio media stream -> runs the voice agent
  POST /twilio/recording   Twilio recording-completed callback
  GET  /dashboard          Lead dashboard (admin sees all clients; each client sees only theirs)
  GET  /healthz            Health check for your host / uptime monitor
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import hmac
import secrets
from contextlib import asynccontextmanager
from xml.sax.saxutils import escape

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import PlainTextResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from loguru import logger
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport
from twilio.request_validator import RequestValidator

from app import dashboard, twilio_api
from app.clients import ClientProfile, ClientRegistry, normalize_phone
from app.config import settings
from app.db import Database
from app.serializer import HandoffAwareTwilioSerializer
from app.session import CallSession

# INFO by default: DEBUG logs include prompts and caller speech.
logger.remove()
logger.add(sys.stderr, level=os.getenv("LOG_LEVEL", "INFO"))

state: dict = {"active_calls": 0, "bg": set()}


@asynccontextmanager
async def lifespan(app: FastAPI):
    for p in settings.problems():
        logger.warning(f"CONFIG: {p}")
    state["db"] = Database(settings.database_path)
    state["clients"] = ClientRegistry(settings.clients_dir)
    logger.info(f"Loaded {len(state['clients'].all())} client profile(s)")
    yield
    if state["bg"]:
        logger.info(f"Waiting for {len(state['bg'])} post-call job(s) to finish")
        await asyncio.wait(state["bg"], timeout=30)


app = FastAPI(title="Real Estate Voice Agent", lifespan=lifespan)
security = HTTPBasic()


# --------------------------------------------------------------------------- helpers
def ws_token(call_sid: str) -> str:
    return hmac.new(settings.app_secret.encode(), call_sid.encode(), hashlib.sha256).hexdigest()[:32]


async def verify_twilio(request: Request) -> dict:
    form = dict(await request.form())
    if settings.validate_twilio_signature:
        url = settings.public_base_url + request.url.path
        if request.url.query:
            url += "?" + request.url.query
        sig = request.headers.get("X-Twilio-Signature", "")
        if not RequestValidator(settings.twilio_auth_token).validate(url, form, sig):
            logger.warning(f"Rejected request with bad Twilio signature on {request.url.path}")
            raise HTTPException(status_code=403, detail="Invalid signature")
    return form


def twiml(body: str) -> Response:
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>', media_type="application/xml")


def fallback_twiml(client: ClientProfile) -> str:
    """What Twilio does if the AI can't take the call (busy, error, crash)."""
    return twilio_api.fallback_verbs(client.transfer_number, client.phone_numbers[0])


# --------------------------------------------------------------------------- Twilio
@app.get("/healthz")
async def healthz():
    return {"ok": True, "active_calls": state["active_calls"], "config_problems": settings.problems()}


@app.post("/twilio/voice")
async def twilio_voice(form: dict = Depends(verify_twilio)):
    call_sid = form.get("CallSid", "")
    from_number = normalize_phone(form.get("From"))
    to_number = normalize_phone(form.get("To"))
    client: ClientProfile | None = state["clients"].by_number(to_number)

    if client is None:
        logger.error(f"No client profile for number {to_number}")
        return twiml("<Say>Sorry, this number is not configured yet. Goodbye.</Say><Hangup/>")

    if state["active_calls"] >= settings.max_concurrent_calls:
        logger.warning("At max concurrent calls; using fallback")
        return twiml(fallback_twiml(client))

    ws_url = settings.public_base_url.replace("https://", "wss://").replace("http://", "ws://") + "/twilio/ws"
    params = {"token": ws_token(call_sid), "from": from_number, "to": to_number}
    param_xml = "".join(f'<Parameter name="{k}" value="{escape(v)}"/>' for k, v in params.items())
    # After <Connect> ends (e.g. if the AI crashes), Twilio continues to the fallback verbs.
    return twiml(f'<Connect><Stream url="{escape(ws_url)}">{param_xml}</Stream></Connect>{fallback_twiml(client)}')


@app.websocket("/twilio/ws")
async def twilio_ws(websocket: WebSocket):
    await websocket.accept()
    try:
        _transport_type, call_data = await parse_telephony_websocket(websocket)
    except Exception as e:
        logger.error(f"Bad media stream handshake: {e}")
        await websocket.close()
        return

    body = call_data.body or {}
    call_sid = call_data.call_id or ""
    if not call_sid or not hmac.compare_digest(str(body.get("token", "")), ws_token(call_sid)):
        logger.warning("Rejected media stream with invalid token")
        await websocket.close()
        return

    client = state["clients"].by_number(body.get("to"))
    if client is None:
        await websocket.close()
        return

    db: Database = state["db"]
    session = CallSession(
        call_sid=call_sid,
        client=client,
        caller_number=body.get("from", ""),
        called_number=body.get("to", ""),
        db=db,
        transfer_available=bool(client.transfer_number) and client.is_open(),
    )
    await db.call_started(call_sid, client.id, session.caller_number, session.called_number)

    serializer = HandoffAwareTwilioSerializer(
        session=session,
        stream_sid=call_data.stream_id,
        call_sid=call_sid,
        account_sid=settings.twilio_account_sid,
        auth_token=settings.twilio_auth_token,
    )
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=serializer,
        ),
    )

    if client.record_calls:
        _bg(twilio_api.start_recording(call_sid))

    from app.bot import run_call  # imported lazily: loads the VAD model on first call
    from app.postcall import finalize_call

    state["active_calls"] += 1
    try:
        await run_call(transport, session)
    except Exception as e:
        logger.exception(f"[{call_sid}] pipeline crashed: {e}")
        session.end_reason = session.end_reason or "error"
    finally:
        state["active_calls"] -= 1
        _bg(finalize_call(session))


@app.post("/twilio/recording")
async def twilio_recording(form: dict = Depends(verify_twilio)):
    sid, url = form.get("CallSid"), form.get("RecordingUrl")
    if sid and url:
        await state["db"].update_call(sid, recording_url=url + ".mp3")
    return PlainTextResponse("ok")


def _bg(coro) -> None:
    task = asyncio.create_task(coro)
    state["bg"].add(task)
    task.add_done_callback(state["bg"].discard)


# --------------------------------------------------------------------------- Dashboard
def current_user(creds: HTTPBasicCredentials = Depends(security)) -> ClientProfile | None:
    """Returns None for the admin (sees everything) or the client profile."""
    if settings.admin_password and secrets.compare_digest(creds.username, settings.admin_username) and secrets.compare_digest(
        creds.password, settings.admin_password
    ):
        return None
    c = state["clients"].authenticate(creds.username, creds.password)
    if c:
        return c
    raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})


@app.get("/dashboard")
async def dashboard_home(tier: str | None = None, client_id: str | None = None, user=Depends(current_user)):
    scope = user.id if user else client_id
    calls = await state["db"].list_calls(client_id=scope, tier=tier)
    stats = await state["db"].stats(client_id=scope)
    clients = [] if user else state["clients"].all()
    return Response(dashboard.render_list(calls, stats, user, clients, tier, scope), media_type="text/html")


@app.get("/dashboard/call/{call_sid}")
async def dashboard_call(call_sid: str, user=Depends(current_user)):
    call = await state["db"].get_call(call_sid)
    if not call or (user and call["client_id"] != user.id):
        raise HTTPException(status_code=404)
    return Response(dashboard.render_call(call), media_type="text/html")


@app.get("/dashboard/call/{call_sid}/recording")
async def dashboard_recording(call_sid: str, user=Depends(current_user)):
    """Proxy the recording from Twilio so clients never need Twilio credentials."""
    call = await state["db"].get_call(call_sid)
    if not call or (user and call["client_id"] != user.id) or not call.get("recording_url"):
        raise HTTPException(status_code=404)
    audio = await twilio_api.fetch_media(call["recording_url"])
    if audio is None:
        raise HTTPException(status_code=502, detail="Recording unavailable")
    return Response(audio, media_type="audio/mpeg")


@app.get("/dashboard/export.csv")
async def dashboard_export(user=Depends(current_user)):
    calls = await state["db"].list_calls(client_id=user.id if user else None, limit=10000)
    return Response(
        dashboard.to_csv(calls),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads.csv"},
    )


@app.post("/admin/reload-clients")
async def reload_clients(user=Depends(current_user)):
    if user is not None:
        raise HTTPException(status_code=403)
    state["clients"].reload()
    return {"clients": [c.id for c in state["clients"].all()]}
