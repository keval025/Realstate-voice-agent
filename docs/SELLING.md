# Selling and Running This as a Service

## The offer (what the client buys)

> "Never miss a lead again. Your AI receptionist answers every call in two rings, 24/7, qualifies buyers and sellers, books appointments on your calendar, and texts you the hot ones with a summary, all in your brand's name."

Why real estate teams pay for it: leads that aren't answered quickly often go to the next agent they call. Most solo agents and small teams miss calls during showings, evenings and weekends. One extra closed deal a year covers years of the service.

**Deliverables:**
- A local phone number (or their existing number forwarded)
- A custom AI receptionist with their name, areas and FAQs
- Calendar booking, live transfer during office hours, instant lead alerts
- CRM integration
- A lead dashboard with transcripts and recordings
- Monthly report: calls, hot leads, booked appointments

## Your cost per client (estimate, so check current provider pricing)

| Item | Rough cost |
|---|---|
| Twilio local number | about $1–2 / month |
| Per call minute (Twilio + Deepgram + Claude + Cartesia) | about $0.05–0.12 / minute in total |
| Your server (shared by all clients) | $6–25 / month |
| A2P 10DLC registration (if SMS alerts) | one-time fee plus small monthly fees |

A typical small team gets 100–300 calls a month at about 3 minutes each, which works out to roughly **$15–110 per month in usage**. Build your own spreadsheet from your actual provider invoices after the first month.

## Pricing models (pick one)

| Model | Example | Good for |
|---|---|---|
| Setup + monthly | $500–1,500 setup + $297–597/mo including N minutes, then $0.25/min | Most clients, predictable |
| Per booked appointment | $X per booked consultation | Clients who want pure results |
| White-label to brokerages | Per-agent seat pricing | Scaling up |

Charge for **setup**: it covers onboarding, script tuning and test calls. Offer a **14-day pilot** so they can hear real calls before committing.

## Demo that closes

1. Before the sales call, set up a demo profile with *their* business name and areas (10 minutes with a YAML file).
2. On the call: "Call this number right now and pretend you're a buyer."
3. While they talk, open the dashboard. When they hang up, show the transcript, score and summary, and the SMS that just reached their phone.

## Client onboarding form (send before setup)

- Business name, the AI's name (e.g. "Ava"), the name of the agent leads should expect
- Service areas (cities and neighborhoods), property types they handle
- Office hours and timezone
- Live transfer number (cell), alert numbers, alert threshold (hot only / warm and up)
- Cal.com account + event type (or "callback only")
- CRM (Follow Up Boss, kvCORE, Lofty, GoHighLevel, HubSpot, Sheets) + access for Zapier/Make
- 5–10 FAQs and their answers (office address, consultation is free, lender referrals, etc.)
- Use a new number, or forward the existing one (always, or only on no answer / after hours)?
- Record calls? (yes by default, disclosed in the greeting)

## Compliance (US), in plain words

This is general information, not legal advice. Have the client confirm with their broker or attorney.

- **AI disclosure:** the greeting says it's a virtual assistant. Several states require disclosure, and it's good practice everywhere.
- **Call recording:** some states require all parties' consent (for example California, Florida and Washington). The greeting discloses recording, and `record_calls: false` turns recording off.
- **Fair Housing Act:** the prompt forbids steering and comments on protected classes, neighborhood "safety", or school quality. Review transcripts in the first weeks.
- **SMS:** A2P 10DLC registration is required for business texting from local numbers.
- **Outbound calling is a different product:** this system only answers inbound calls. Calling leads with AI voices brings in TCPA consent rules and Do-Not-Call checks. Don't repurpose it for outbound without adding those.
- **Data:** you store caller names, numbers and recordings. Keep the server patched, use strong passwords, back up the database, and add a short data-processing clause to your contract. Delete client data on request.

## Running it week to week

- **Daily (5 min):** skim the dashboard for odd calls; check `/healthz` and uptime alerts.
- **Weekly:** read 5–10 transcripts per client, fix prompt or FAQ gaps, and re-test with `scripts/chat_test.py`.
- **Monthly:** send each client a report (calls, hot/warm counts, bookings), compare provider invoices with what you charge, and update dependencies on a test server first.

## Contract essentials

Scope (inbound answering only), pricing and included minutes, overage rate, 30-day cancellation, no guarantee of closed deals, the client owns their lead data, you're a data processor, and target uptime of "best effort, 99%" with human fallback forwarding.
