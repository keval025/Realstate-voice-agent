"""HTTP-level tests: Twilio signature check, TwiML, dashboard auth."""

import os

os.environ.update(
    PUBLIC_BASE_URL="https://agent.example.com", TWILIO_AUTH_TOKEN="tok", TWILIO_ACCOUNT_SID="AC1",
    APP_SECRET="s" * 32, ADMIN_PASSWORD="adminpw", VALIDATE_TWILIO_SIGNATURE="true",
)

import importlib  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from twilio.request_validator import RequestValidator  # noqa: E402


def client(tmp_path):
    os.environ["DATABASE_PATH"] = str(tmp_path / "t.db")
    import app.config
    importlib.reload(app.config)
    import app.server
    importlib.reload(app.server)
    return TestClient(app.server.app)


def test_voice_webhook_signature(tmp_path):
    with client(tmp_path) as c:
        form = {"CallSid": "CA9", "From": "+15125550000", "To": "+15125550123"}
        assert c.post("/twilio/voice", data=form).status_code == 403
        sig = RequestValidator("tok").compute_signature("https://agent.example.com/twilio/voice", form)
        r = c.post("/twilio/voice", data=form, headers={"X-Twilio-Signature": sig})
        assert r.status_code == 200
        assert "wss://agent.example.com/twilio/ws" in r.text and "<Dial" in r.text


def test_unknown_number(tmp_path):
    with client(tmp_path) as c:
        form = {"CallSid": "CA9", "From": "+15125550000", "To": "+19999999999"}
        sig = RequestValidator("tok").compute_signature("https://agent.example.com/twilio/voice", form)
        r = c.post("/twilio/voice", data=form, headers={"X-Twilio-Signature": sig})
        assert "not configured" in r.text


def test_dashboard_auth(tmp_path):
    with client(tmp_path) as c:
        assert c.get("/dashboard").status_code == 401
        assert c.get("/dashboard", auth=("admin", "wrong")).status_code == 401
        r = c.get("/dashboard", auth=("admin", "adminpw"))
        assert r.status_code == 200 and "Leads" in r.text
        assert c.get("/dashboard/export.csv", auth=("admin", "adminpw")).text.startswith("started_at")
