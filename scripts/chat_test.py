"""Test the agent's brain by TYPING instead of talking (cheap and fast).

Uses the exact same prompt and tools as phone calls. Only needs ANTHROPIC_API_KEY
(plus the client's Cal.com key if you want to test real booking).

    python scripts/chat_test.py
    python scripts/chat_test.py --client sunrise-realty --transfer
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anthropic import AsyncAnthropic  # noqa: E402
from pipecat.adapters.schemas.direct_function import DirectFunctionWrapper  # noqa: E402
from pipecat.frames.frames import EndWorkerFrame, TTSSpeakFrame  # noqa: E402

from app.clients import ClientRegistry  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.leads import score_lead  # noqa: E402
from app.prompts import greeting, system_prompt  # noqa: E402
from app.session import CallSession  # noqa: E402
from app.tools import ALL_TOOLS  # noqa: E402


class PrintLLM:
    ended = False

    async def push_frame(self, frame, *a, **k):
        if isinstance(frame, TTSSpeakFrame):
            print(f"  Assistant (filler): {frame.text}")
        if isinstance(frame, EndWorkerFrame):
            PrintLLM.ended = True


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client")
    ap.add_argument("--transfer", action="store_true", help="pretend office is open and transfer works")
    args = ap.parse_args()

    reg = ClientRegistry(settings.clients_dir)
    client = reg.get(args.client) if args.client else reg.all()[0]
    db = Database("data/chat_test.db")
    session = CallSession(call_sid="CHAT-TEST", client=client, caller_number="+15125550000",
                          called_number=client.phone_numbers[0], db=db, transfer_available=args.transfer)
    await db.call_started(session.call_sid, client.id, session.caller_number, session.called_number)

    tools, handlers = [], {}
    for fn in ALL_TOOLS:
        schema = DirectFunctionWrapper(fn).to_function_schema()
        tools.append({"name": schema.name, "description": schema.description,
                      "input_schema": {"type": "object", "properties": schema.properties, "required": schema.required}})
        handlers[schema.name] = fn

    ai = AsyncAnthropic(api_key=settings.anthropic_api_key)
    system = system_prompt(client, session.caller_number, session.transfer_available)
    hello = greeting(client)
    print(f"\nAssistant: {hello}\n(type 'quit' to stop)\n")
    messages = [{"role": "user", "content": "(call connected)"}, {"role": "assistant", "content": hello}]

    while not PrintLLM.ended:
        text = input("You: ").strip()
        if text.lower() in ("quit", "exit"):
            break
        messages.append({"role": "user", "content": text})
        while True:
            resp = await ai.messages.create(model=settings.anthropic_model, max_tokens=300, system=system,
                                            tools=tools, messages=messages)
            messages.append({"role": "assistant", "content": [b.model_dump() for b in resp.content]})
            results = []
            for b in resp.content:
                if b.type == "text" and b.text.strip():
                    print(f"Assistant: {b.text.strip()}")
                if b.type == "tool_use":
                    out = {}

                    async def cb(result, properties=None, _out=out):
                        _out.update(result=result)

                    params = SimpleNamespace(app_resources=session, result_callback=cb, llm=PrintLLM())
                    print(f"  [tool] {b.name}({json.dumps(b.input)})")
                    await handlers[b.name](params, **b.input)
                    print(f"  [result] {json.dumps(out.get('result'))}")
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(out.get("result"))})
            if not results:
                break
            messages.append({"role": "user", "content": results})
            if PrintLLM.ended:
                break

    sc = score_lead(session.lead)
    print("\n--- Lead captured ---")
    print(json.dumps(session.lead.to_dict(), indent=2))
    print(f"Score {sc.points}/100 -> {sc.tier.upper()} ({', '.join(sc.reasons)})")


if __name__ == "__main__":
    asyncio.run(main())
