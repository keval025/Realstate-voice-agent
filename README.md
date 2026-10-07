# AI Phone Receptionist for Real Estate Teams

An AI voice agent that answers a real estate team's phone 24/7, qualifies callers (buyers and sellers), books appointments on the agent's calendar, live-transfers hot leads during office hours, and sends every lead to the team's CRM and phone, with a summary and a hot/warm/cold score.

One server can run many clients. Each client is one YAML file.

```
Caller ──► Twilio number ──► /twilio/voice (TwiML) ──► /twilio/ws (audio stream)
                                                          │
             Deepgram (speech-to-text) ◄──────────────────┤
             Claude (conversation + tools) ───────────────┤──► Cal.com (book)
             Cartesia (text-to-speech) ───────────────────┘──► Twilio (transfer)
                                                          │
After hang-up: Claude summary + lead score ──► SQLite ──► Dashboard, CRM webhook, SMS alert
```

## What's included

| Feature | Where |
|---|---|
| Natural voice conversation, interruptions, end-of-turn detection | `app/bot.py` (Pipecat) |
| Real estate qualification script, Fair Housing guardrails, AI and recording disclosure | `app/prompts.py` |
| Live tools: save lead, check calendar, book, transfer to human, hang up | `app/tools.py` |
| Deterministic lead scoring (hot / warm / cold / disqualified) | `app/leads.py` |
| Post-call summary, gap-filling extraction, CRM webhook, SMS alert | `app/postcall.py`, `app/notify.py` |
| Multi-client dashboard with transcripts, recordings and CSV export | `app/dashboard.py` |
| Twilio signature checks, signed media-stream tokens, per-client logins | `app/server.py` |
| Fallbacks: AI outage or over capacity → forwards to a human or voicemail | `app/server.py`, `app/bot.py` |
| Caller silence handling, max call length, no talking over the disclosure | `app/bot.py` |
| Browser test mode (no phone number needed) | `dev.py` |
| Text chat test mode (cheapest way to test the script) | `scripts/chat_test.py` |
| Tests | `tests/` (`pytest`) |

---

## Part 1: Accounts you need (about 30 minutes)

| Service | What for | Sign up |
|---|---|---|
| Twilio | Phone number + calls (+ SMS alerts) | twilio.com |
| Anthropic | Claude, the brain | console.anthropic.com |
| Deepgram | Speech-to-text | console.deepgram.com |
| Cartesia | Voice | play.cartesia.ai |
| Cal.com (per client) | Booking | cal.com (the client creates this) |

Put a little credit on each one. Twilio trial accounts can only call verified numbers, so upgrade Twilio before going live.

## Part 2: Run it on your computer

You need Python 3.11+ installed.

```bash
cd realestate-voice-agent
python -m venv .venv
# Windows:  .venv\Scripts\activate      Mac/Linux:  source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env        # Mac/Linux: cp .env.example .env
```

Open `.env` and paste your API keys. For `APP_SECRET` and `ADMIN_PASSWORD`, generate random strings:

```bash
python -c "import secrets;print(secrets.token_urlsafe(32))"
```

Run the tests to check everything installed correctly:

```bash
pip install pytest httpx
pytest -q
```

### Test 1: type to the agent (cheapest)

```bash
python scripts/chat_test.py
```

Play a caller: a hot buyer, a seller, someone already working with an agent, a wrong number, someone asking "is this a safe neighborhood?" (it should decline per Fair Housing). At the end it prints the lead it captured and the score.

### Test 2: talk to it in your browser

```bash
python dev.py
```

Open http://localhost:7860/client, allow the microphone, and talk. Finished calls appear in the dashboard.

### Test 3: a real phone call through ngrok

1. Install ngrok (ngrok.com) and run `ngrok http 8000`. Copy the `https://....ngrok-free.app` URL into `PUBLIC_BASE_URL` in `.env`.
2. Start the server: `uvicorn app.server:app --port 8000`
3. In Twilio: buy a local number → *Configure* → **A call comes in** → Webhook → `https://YOUR-URL/twilio/voice` (HTTP POST). Save.
4. Put that number in `clients/sunrise-realty.yaml` under `phone_numbers`.
5. Call the number from your phone.
6. Open `https://YOUR-URL/dashboard` (log in with `ADMIN_USERNAME` / `ADMIN_PASSWORD`).

---

## Part 3: Deploy to production

The server needs: always-on, HTTPS, WebSockets, and a persistent disk for `data/`. Two good options:

### Option A: a small VPS (cheapest and most control, about $6–12 per month)

DigitalOcean, Hetzner, Vultr, or AWS Lightsail. Choose **2 vCPU / 2–4 GB RAM** and a **US region** close to your providers (US-East is a good default). Lower latency means a more natural conversation.

```bash
# on the server (Ubuntu)
curl -fsSL https://get.docker.com | sh
git clone <your private repo> agent && cd agent
nano .env                                # paste production values
docker compose up -d --build
```

Point a domain such as `voice.yourdomain.com` at the server IP and put Caddy in front for automatic HTTPS:

```bash
sudo apt install -y caddy
echo 'voice.yourdomain.com {
  reverse_proxy localhost:8000
}' | sudo tee /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Set `PUBLIC_BASE_URL=https://voice.yourdomain.com`, run `docker compose up -d` again, and update the Twilio webhook URL.

### Option B: Railway or Render (easiest)

Create a new service from your GitHub repo (it detects the `Dockerfile`). Add all `.env` values as environment variables, **attach a volume mounted at `/srv/data`**, and set `PUBLIC_BASE_URL` to the service URL. Don't use a free or sleeping tier, because calls will fail while the service wakes up.

### Production checklist

- [ ] `https://YOUR-URL/healthz` shows `"config_problems": []`
- [ ] `VALIDATE_TWILIO_SIGNATURE=true`
- [ ] Twilio webhook uses the exact `PUBLIC_BASE_URL` (signature checks fail if the URL differs)
- [ ] Twilio number → *Configure* → **Primary handler fails** → set a fallback that forwards to the client's cell, for when your whole server is down
- [ ] Uptime monitor (UptimeRobot or Better Stack, free) pinging `/healthz` every minute
- [ ] Daily backup of `data/leads.db` (for example a cron job that copies it to S3 or Backblaze)
- [ ] Spend limits or alerts set in Twilio, Anthropic, Deepgram and Cartesia
- [ ] Test calls: hot buyer, seller, after-hours transfer request, silence, hang-up mid-call, "already have an agent"

---

## Part 4: Onboard a new client (about 1 hour per client)

1. **Copy** `clients/sunrise-realty.yaml` to `clients/<client-id>.yaml` and fill it in with the client (see the onboarding form in `docs/SELLING.md`).
2. **Phone number:** buy a local Twilio number in their area code and set its webhook to `/twilio/voice`. Then either
   - give them the new number for ads, website and signs, or
   - have them **forward** their existing line to it (all calls, or only no-answer / after-hours forwarding, which is set up at their phone carrier).
3. **Calendar:** the client creates a free Cal.com account, connects their Google or Outlook calendar, and creates an event type such as "15 min consultation". They send you the event type id (from the URL) and an API key (Settings → Developer → API Keys). Add the key to `.env` as `CALCOM_API_KEY_<CLIENT>` and put that variable name in their YAML.
4. **CRM:** create a Zapier or Make "Catch Hook" (or a GoHighLevel inbound webhook) and paste the URL into `crm_webhook_url`. Map the fields to their CRM (Follow Up Boss, kvCORE, Lofty, GoHighLevel, HubSpot, Google Sheets…). Each request carries an `X-Signature-SHA256` header (HMAC of the body with `APP_SECRET`) if they want to verify it.
5. **SMS alerts:** US business texting requires **A2P 10DLC registration** in Twilio (brand + campaign, which takes a few days). Until it's approved, use the CRM webhook to send email alerts instead.
6. **Dashboard login:** `python scripts/hash_password.py "their-password"`, then put the result in `dashboard_password_hash` with a `dashboard_username`.
7. **Reload** without restarting: `curl -X POST -u admin:PASSWORD https://YOUR-URL/admin/reload-clients` (or restart the container).
8. **Test-call** the number 5 times with different scenarios before handing it over.

### Webhook payload (sent after every call that has a conversation)

```json
{
  "call_sid": "CA...", "client_id": "sunrise-realty", "client_name": "Sunrise Realty Group",
  "caller_number": "+15125550000",
  "lead": {"name": "Sam", "email": "", "callback_number": "+15125550000", "intent": "buy",
           "areas": "Round Rock", "budget": "400 to 450 thousand", "timeline": "within 2 months",
           "financing": "pre_approved", "property_type": "single family", "bedrooms": "3",
           "has_agent": "no", "motivation": "relocating for work", "notes": "", "property_address": ""},
  "score": 82, "tier": "hot", "score_reasons": ["moving within 3 months", "pre-approved"],
  "summary": "Sam is relocating to Round Rock for work...", "next_step": "Send 3-bed listings under 450k...",
  "appointment_at": "2026-10-08T15:00:00Z", "booking_uid": "...", "transferred": false,
  "duration_sec": 184, "ended_at": "...", "transcript": [{"t": 1.2, "role": "assistant", "text": "..."}]
}
```

---

## Part 5: Customizing

- **Script / personality:** `app/prompts.py`. Change one thing at a time and re-test with `scripts/chat_test.py`.
- **Scoring weights / tiers:** `score_lead()` in `app/leads.py` (tests in `tests/test_core.py`).
- **Voice:** pick a voice at play.cartesia.ai and put its id in the client's `voice_id`.
- **Model:** `ANTHROPIC_MODEL` in `.env`. A fast model keeps replies snappy; test before switching to a bigger one.
- **Spanish callers:** set Deepgram `language="multi"` in `app/bot.py`, choose a multilingual Cartesia voice, and add "Reply in the caller's language" to the prompt.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Call says "application error" | Check server logs; check that the Twilio webhook URL is correct and the server is reachable |
| 403 on `/twilio/voice` | `PUBLIC_BASE_URL` doesn't exactly match the URL Twilio calls (http vs https, trailing slash) |
| Silence after greeting | Check Deepgram/Cartesia keys and credit; the call auto-forwards to the fallback when a provider is down |
| Agent talks over the caller or cuts them off | Background noise; ask callers to use a quieter line, or add Krisp noise filtering |
| Booking fails | Check the Cal.com key and event type id, and that the event has availability in the next 5 days |
| Transfer rings nobody | Check `transfer_number` and `business_hours` (in the client's timezone) |

## Scaling notes

- One 2 vCPU server handles roughly 10–20 simultaneous calls. Set `MAX_CONCURRENT_CALLS`; extra calls go to the human fallback, never a busy signal.
- Past roughly 20 clients or several servers: move SQLite to Postgres (only `app/db.py` changes) and run multiple containers behind a load balancer.
- Pipecat moves fast. This code was written against **pipecat-ai 1.12**. Pin the version in `requirements.txt` and run `pytest` after any upgrade.
