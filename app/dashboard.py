"""Server-rendered lead dashboard. No build step, no JavaScript framework."""

from __future__ import annotations

import csv
import io
from html import escape as e
from urllib.parse import urlencode

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#16181d;--mute:#667085;--line:#e4e7ec;--hot:#d92d20;--warm:#dc6803;--cold:#475467;--dq:#98a2b3;--ok:#067647}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--ink:#eceef2;--mute:#98a2b3;--line:#2a2f3a}}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink)}
.wrap{max-width:1180px;margin:0 auto;padding:24px 16px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:24px 0 8px}.mute{color:var(--mute)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;margin:18px 0}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.stat b{display:block;font-size:24px}
.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:8px 0 12px}
.bar a,.bar select{padding:6px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);text-decoration:none}
.bar a.on{border-color:var(--ink)}
.tbl{width:100%;overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;min-width:760px}th,td{padding:10px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--mute)}tr:last-child td{border-bottom:0}
td a{color:inherit}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600;color:#fff}
.hot{background:var(--hot)}.warm{background:var(--warm)}.cold{background:var(--cold)}.disqualified,.none{background:var(--dq)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:14px}
.kv{display:grid;grid-template-columns:160px 1fr;gap:6px 12px}.kv div:nth-child(odd){color:var(--mute)}
.line{padding:6px 0;border-bottom:1px dashed var(--line)}.line:last-child{border:0}.who{font-weight:600;margin-right:6px}
@media (max-width:600px){.kv{grid-template-columns:1fr}}
"""


def page(title: str, body: str) -> str:
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{e(title)}</title><style>{CSS}</style></head><body><div class='wrap'>{body}</div></body></html>"
    )


def pill(tier: str | None) -> str:
    t = tier or "none"
    return f"<span class='pill {e(t)}'>{e(t.upper() if tier else 'N/A')}</span>"


def render_list(calls, stats, user, clients, tier, scope) -> str:
    title = user.business_name if user else "All clients"
    s = lambda k: stats.get(k, 0)  # noqa: E731
    stat_html = "".join(
        f"<div class='stat'><span class='mute'>{lbl}</span><b>{val}</b></div>"
        for lbl, val in [
            ("Total calls", s("total")),
            ("Hot", s("hot")),
            ("Warm", s("warm")),
            ("Cold", s("cold")),
            ("Booked", s("booked")),
        ]
    )

    def link(label, t):
        q = {k: v for k, v in {"tier": t, "client_id": scope if not user else None}.items() if v}
        return f"<a class='{'on' if tier == t else ''}' href='/dashboard?{urlencode(q)}'>{label}</a>"

    filters = link("All", None) + link("Hot", "hot") + link("Warm", "warm") + link("Cold", "cold")
    if clients:
        opts = "<option value=''>All clients</option>" + "".join(
            f"<option value='{e(c.id)}' {'selected' if c.id == scope else ''}>{e(c.business_name)}</option>"
            for c in clients
        )
        filters += (
            "<form method='get' action='/dashboard' style='margin:0'>"
            f"<select name='client_id' onchange='this.form.submit()'>{opts}</select></form>"
        )
    filters += "<a href='/dashboard/export.csv'>Export CSV</a>"

    rows = []
    for c in calls:
        lead = c.get("lead_json") or {}
        if not isinstance(lead, dict):
            lead = {}
        what = " · ".join(x for x in [lead.get("intent"), lead.get("areas") or lead.get("property_address"), lead.get("budget")] if x)
        rows.append(
            "<tr>"
            f"<td>{e((c.get('started_at') or '')[:16].replace('T', ' '))}</td>"
            f"<td>{pill(c.get('tier'))} <span class='mute'>{c.get('score') if c.get('score') is not None else ''}</span></td>"
            f"<td><a href='/dashboard/call/{e(c['call_sid'])}'>{e(lead.get('name') or 'Unknown')}</a><br>"
            f"<span class='mute'>{e(c.get('from_number') or '')}</span></td>"
            f"<td>{e(what)}<br><span class='mute'>{e(lead.get('timeline') or '')}</span></td>"
            f"<td>{'Booked ' + e((c.get('appointment_at') or '')[:16].replace('T', ' ')) if c.get('appointment_at') else ('Transferred' if c.get('transferred') else e(c.get('status') or ''))}</td>"
            f"<td class='mute'>{e((c.get('summary') or '')[:140])}</td>"
            "</tr>"
        )
    table = (
        "<div class='tbl'><table><tr><th>When (UTC)</th><th>Tier</th><th>Caller</th><th>Looking for</th>"
        f"<th>Outcome</th><th>Summary</th></tr>{''.join(rows) or '<tr><td colspan=6 class=mute>No calls yet.</td></tr>'}</table></div>"
    )
    return page("Leads", f"<h1>Leads</h1><div class='mute'>{e(title)}</div><div class='stats'>{stat_html}</div><div class='bar'>{filters}</div>{table}")


def render_call(c: dict) -> str:
    lead = c.get("lead_json") if isinstance(c.get("lead_json"), dict) else {}
    kv = "".join(f"<div>{e(k.replace('_', ' ').title())}</div><div>{e(str(v))}</div>" for k, v in lead.items() if v)
    reasons = c.get("score_reasons") if isinstance(c.get("score_reasons"), list) else []
    meta = [
        ("Caller ID", c.get("from_number")),
        ("Started (UTC)", c.get("started_at")),
        ("Duration", f"{c.get('duration_sec') or 0} sec"),
        ("Outcome", c.get("status")),
        ("End reason", c.get("end_reason")),
        ("Appointment (UTC)", c.get("appointment_at")),
        ("CRM", c.get("crm_status")),
        ("SMS alert", c.get("alert_status")),
    ]
    meta_html = "".join(f"<div>{e(k)}</div><div>{e(str(v))}</div>" for k, v in meta if v)
    rec = f"<audio controls preload='none' src='/dashboard/call/{e(c['call_sid'])}/recording' style='width:100%'></audio>" if c.get("recording_url") else ""
    lines = c.get("transcript_json") if isinstance(c.get("transcript_json"), list) else []
    tx = "".join(
        f"<div class='line'><span class='who'>{'Caller' if l.get('role') == 'user' else 'Assistant'}</span>{e(l.get('text', ''))}</div>"
        for l in lines
    ) or "<span class='mute'>No transcript.</span>"
    body = (
        "<a href='/dashboard' class='mute'>&larr; All leads</a>"
        f"<h1>{e(lead.get('name') or 'Unknown caller')} {pill(c.get('tier'))}</h1>"
        f"<div class='mute'>Score {c.get('score') or 0}/100 · {e(', '.join(reasons))}</div>"
        f"<div class='card' style='margin-top:14px'><b>Summary</b><p>{e(c.get('summary') or '')}</p>"
        f"<b>Next step</b><p>{e(c.get('next_step') or '')}</p></div>"
        f"<div class='card'><div class='kv'>{kv}{meta_html}</div></div>"
        f"{('<div class=card>' + rec + '</div>') if rec else ''}"
        f"<h2>Transcript</h2><div class='card'>{tx}</div>"
    )
    return page("Call detail", body)


CSV_COLS = [
    "started_at", "client_id", "from_number", "tier", "score", "status", "appointment_at", "summary", "next_step",
]
LEAD_COLS = ["name", "email", "callback_number", "intent", "areas", "property_address", "budget", "timeline", "financing", "property_type", "bedrooms", "has_agent", "motivation", "notes"]


def to_csv(calls: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLS + LEAD_COLS)
    for c in calls:
        lead = c.get("lead_json") if isinstance(c.get("lead_json"), dict) else {}
        w.writerow([c.get(k, "") for k in CSV_COLS] + [lead.get(k, "") for k in LEAD_COLS])
    return buf.getvalue()
