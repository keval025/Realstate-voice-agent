"""Twilio serializer that doesn't hang up calls we've handed off to a human.

Pipecat hangs up the Twilio call whenever the pipeline ends. After a live
transfer (or a fallback redirect) the pipeline ends *because* the call moved
to the human, so hanging up would cut the transfer. We skip it in that case.
"""

from pipecat.serializers.twilio import TwilioFrameSerializer

from app.session import CallSession


class HandoffAwareTwilioSerializer(TwilioFrameSerializer):
    def __init__(self, *args, session: CallSession, **kwargs):
        super().__init__(*args, **kwargs)
        self._session = session

    async def _hang_up_call(self):  # overrides Pipecat's private hook (checked against pipecat 1.12)
        if self._session.handoff_pending:
            return
        await super()._hang_up_call()
