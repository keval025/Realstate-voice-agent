"""The real-time voice pipeline for one call.

    caller audio -> Deepgram (speech-to-text) -> Claude (+ tools) -> Cartesia (text-to-speech) -> caller
"""

from __future__ import annotations

import asyncio

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import EndWorkerFrame, LLMMessagesAppendFrame, TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    AssistantTurnStoppedMessage,
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
    UserTurnStoppedMessage,
)
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.transports.base_transport import BaseTransport
from pipecat.turns.user_mute import FunctionCallUserMuteStrategy, MuteUntilFirstBotCompleteUserMuteStrategy
from pipecat.workers.runner import WorkerRunner

from app import twilio_api
from app.config import settings
from app.prompts import greeting, system_prompt
from app.session import CallSession
from app.tools import ALL_TOOLS


async def run_call(transport: BaseTransport, session: CallSession, telephony: bool = True) -> None:
    client = session.client

    stt = DeepgramSTTService(
        api_key=settings.deepgram_api_key,
        settings=DeepgramSTTService.Settings(
            model=settings.deepgram_model,
            language="en-US",
            smart_format=True,
            numerals=True,
            # Boost recognition of local place names and the brand.
            keyterm=[client.business_name, *client.service_areas][:50],
        ),
    )

    llm = AnthropicLLMService(
        api_key=settings.anthropic_api_key,
        settings=AnthropicLLMService.Settings(
            model=settings.anthropic_model,
            system_instruction=system_prompt(client, session.caller_number, session.transfer_available),
            max_tokens=300,
            temperature=0.4,
            enable_prompt_caching=True,
        ),
    )

    tts = CartesiaTTSService(
        api_key=settings.cartesia_api_key,
        settings=CartesiaTTSService.Settings(voice=client.voice_id or settings.default_voice_id),
    )

    context = LLMContext(tools=ALL_TOOLS)
    user_agg, assistant_agg = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
            # Don't let the caller cut off the disclosure, and don't interrupt a booking mid-way.
            user_mute_strategies=[MuteUntilFirstBotCompleteUserMuteStrategy(), FunctionCallUserMuteStrategy()],
            user_idle_timeout=settings.user_idle_seconds,
        ),
    )

    pipeline = Pipeline([transport.input(), stt, user_agg, llm, tts, transport.output(), assistant_agg])

    pp = dict(enable_metrics=True, enable_usage_metrics=True)
    if telephony:
        pp.update(audio_in_sample_rate=8000, audio_out_sample_rate=8000)
    worker = PipelineWorker(pipeline, params=PipelineParams(**pp), app_resources=session)
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)

    # ---------------- events ----------------
    @transport.event_handler("on_client_connected")
    async def on_connected(_transport, _client):
        logger.info(f"[{session.call_sid}] caller connected ({session.caller_number}) -> {client.id}")
        # Spoken verbatim so the AI/recording disclosure is always identical.
        await worker.queue_frames([TTSSpeakFrame(greeting(client))])

    @transport.event_handler("on_client_disconnected")
    async def on_disconnected(_transport, _client):
        logger.info(f"[{session.call_sid}] caller disconnected")
        await runner.cancel()

    @worker.event_handler("on_pipeline_error")
    async def on_error(_worker, frame):
        # A provider (speech-to-text, LLM or voice) is down for good. Don't leave the caller in
        # silence: hand the live call to a human, or to voicemail.
        proc = getattr(frame, "processor", None)
        if not telephony or proc is None or proc.is_usable or session.end_reason == "service_failure":
            return
        logger.error(f"[{session.call_sid}] {proc} is down, redirecting call to fallback")
        session.end_reason = "service_failure"
        session.handoff_pending = True
        await twilio_api.redirect_to_fallback(
            session.call_sid, client.transfer_number, client.phone_numbers[0] if client.phone_numbers else ""
        )

    @user_agg.event_handler("on_user_turn_stopped")
    async def on_user_turn(_agg, _strategy, message: UserTurnStoppedMessage):
        session.idle_prompts = 0
        session.add_line("user", message.content)

    @assistant_agg.event_handler("on_assistant_turn_stopped")
    async def on_bot_turn(_agg, message: AssistantTurnStoppedMessage):
        session.add_line("assistant", message.content)

    @user_agg.event_handler("on_user_turn_idle")
    async def on_idle(_agg):
        session.idle_prompts += 1
        if session.idle_prompts == 1:
            await worker.queue_frames(
                [
                    LLMMessagesAppendFrame(
                        messages=[
                            {
                                "role": "developer",
                                "content": "The caller has been silent. Briefly check if they're still there.",
                            }
                        ],
                        run_llm=True,
                    )
                ]
            )
        elif session.idle_prompts >= 2:
            session.end_reason = "caller_silent"
            await worker.queue_frames(
                [
                    TTSSpeakFrame("It seems we got disconnected. Feel free to call back anytime. Goodbye!"),
                    EndWorkerFrame(reason="idle"),
                ]
            )

    async def max_duration_guard():
        await asyncio.sleep(settings.max_call_minutes * 60)
        logger.warning(f"[{session.call_sid}] max call duration reached")
        session.end_reason = "max_duration"
        await worker.queue_frames(
            [
                TTSSpeakFrame(
                    f"I want to be respectful of your time. {client.team_member_name} will follow up with you "
                    "personally. Thanks so much for calling, goodbye!"
                ),
                EndWorkerFrame(reason="max_duration"),
            ]
        )

    guard = asyncio.create_task(max_duration_guard())
    try:
        await runner.run()
    finally:
        guard.cancel()
