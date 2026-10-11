"""Iteration 8 — Weak-Answer Email Alerts via Resend.

Tests /api/admin/test-alert and the background email dispatch on /api/chat
for status in {insufficient, fallback, error}. Uses the backend supervisor
log file to verify the "weak-answer alert sent" vs "weak-answer alert
throttled" lines fired by the fire-and-forget task.

All tests live in a single class so pytest-xdist loadscope keeps them on
ONE worker — the throttle state (_last_alert_ts) is a process-global dict
in the backend, and we depend on ordered execution.
"""
import os
import time
import uuid
import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/") if os.environ.get("REACT_APP_BACKEND_URL") else "https://himt-assistant-bot.preview.emergentagent.com"
API = f"{BASE_URL}/api"
ADMIN_TOKEN = "himt-admin-2026"

# Read Resend config from backend/.env so the test tracks env changes instead of
# hardcoding a stale email address. Falls back to previous default only if unset.
def _read_backend_env(key: str, default: str) -> str:
    try:
        with open("/app/backend/.env", "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == key:
                    return v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return default

RESEND_FROM_EMAIL = _read_backend_env("RESEND_FROM_EMAIL", "onboarding@resend.dev")
WEAK_ANSWER_ALERT_TO = _read_backend_env("WEAK_ANSWER_ALERT_TO", "hnpatel1971@gmail.com")
BACKEND_LOG = "/var/log/supervisor/backend.err.log"
TIMEOUT = 90


def _log_offset() -> int:
    try:
        return os.path.getsize(BACKEND_LOG)
    except FileNotFoundError:
        return 0


def _log_since(offset: int, wait_seconds: float = 2.0) -> str:
    """Return log content appended AFTER offset. Waits briefly for async fire-and-forget task to log."""
    time.sleep(wait_seconds)
    try:
        with open(BACKEND_LOG, "rb") as f:
            f.seek(offset)
            return f.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return ""


@pytest.fixture(scope="class")
def s():
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


@pytest.fixture(scope="class")
def admin_headers():
    return {"X-Admin-Token": ADMIN_TOKEN, "Content-Type": "application/json"}


class TestWeakAnswerAlert:
    """All weak-answer alert tests — MUST run in defined order (throttle state is process-global)."""

    # ---------- 1. Admin test-alert auth ----------
    def test_test_alert_without_token_401(self, s):
        r = s.post(f"{API}/admin/test-alert", timeout=TIMEOUT)
        assert r.status_code == 401
        d = r.json()
        assert "detail" in d and "admin" in d["detail"].lower()

    def test_test_alert_wrong_token_401(self, s):
        r = s.post(f"{API}/admin/test-alert", headers={"X-Admin-Token": "wrong-token"}, timeout=TIMEOUT)
        assert r.status_code == 401

    # ---------- 2. Admin test-alert success + real Resend send + log line ----------
    def test_test_alert_with_token_sends_email_and_logs(self, s, admin_headers):
        offset = _log_offset()
        r = s.post(f"{API}/admin/test-alert", headers=admin_headers, timeout=TIMEOUT)
        assert r.status_code == 200, f"status={r.status_code} body={r.text}"
        d = r.json()

        # Response shape
        assert d.get("ok") is True
        assert d.get("sent_to") == WEAK_ANSWER_ALERT_TO
        assert d.get("from") == RESEND_FROM_EMAIL

        # Backend log line
        log = _log_since(offset, wait_seconds=3.0)
        assert "weak-answer alert sent to" in log, f"expected 'weak-answer alert sent to' in log, got:\n{log}"
        assert WEAK_ANSWER_ALERT_TO in log

    # ---------- 3. After test-alert, insufficient chat within 120s is THROTTLED ----------
    def test_insufficient_chat_after_test_alert_is_throttled(self, s):
        # Unique query so it is NOT cached
        unique = f"What is the current price of Bitcoin on {uuid.uuid4().hex[:8]} exchange today?"
        offset = _log_offset()
        r = s.post(f"{API}/chat", json={"message": unique, "session_id": str(uuid.uuid4())}, timeout=TIMEOUT)
        assert r.status_code == 200
        body = r.json()
        # The chat must NOT crash even though a background email dispatch may run
        assert body["status"] in ("insufficient", "fallback", "error", "grounded")
        # For a Bitcoin question unrelated to HIMT, expect insufficient (rare fallback also ok)
        assert body["status"] in ("insufficient", "fallback"), f"unexpected status for unrelated query: {body['status']}"

        # Because /admin/test-alert (previous test) just fired an alert (sets _last_alert_ts=now),
        # this chat's dispatch MUST be throttled within 120s.
        log = _log_since(offset, wait_seconds=3.5)
        assert "weak-answer alert throttled (min interval)" in log, (
            f"Expected 'throttled' log line for insufficient chat within 120s of prior alert, got:\n{log}"
        )
        # And it must NOT have re-sent
        assert "weak-answer alert sent to" not in log, (
            f"Alert should have been throttled, but a real send fired:\n{log}"
        )

    # ---------- 4. Chat endpoint returns 200 even if send fails (fire-and-forget, never raises) ----------
    def test_chat_endpoint_never_propagates_resend_error(self, s):
        # Repeat within throttle window — dispatch will be throttled (no send), but the important
        # invariant is the chat endpoint always returns 200 regardless of Resend outcome.
        for _ in range(2):
            unique = f"random unrelated query {uuid.uuid4().hex}"
            r = s.post(f"{API}/chat", json={"message": unique, "session_id": str(uuid.uuid4())}, timeout=TIMEOUT)
            assert r.status_code == 200
            j = r.json()
            assert "answer" in j and j["answer"]
            assert "status" in j

    # ---------- 5. Grounded query (HIMT-related) does NOT log any alert line ----------
    def test_grounded_chat_does_not_trigger_alert(self, s):
        # Use a known-grounded intent. It'll likely be cached — cached path skips dispatch entirely.
        # Use a fresh session id but the query is standard, so cache_key will match existing cache.
        offset = _log_offset()
        r = s.post(f"{API}/chat", json={"message": "HIMT contact number", "session_id": str(uuid.uuid4())}, timeout=TIMEOUT)
        assert r.status_code == 200
        d = r.json()
        # Should be grounded
        assert d["status"] == "grounded", f"expected grounded, got {d['status']} — answer was: {d.get('answer','')[:200]}"

        log = _log_since(offset, wait_seconds=2.5)
        # No alert dispatch line should appear
        assert "weak-answer alert sent" not in log, f"grounded chat should not trigger alert, saw:\n{log}"
        assert "weak-answer alert throttled" not in log, (
            f"grounded chat should not even hit dispatch, saw throttle line:\n{log}"
        )

    # ---------- 6. Chat latency: fire-and-forget must not block on Resend ----------
    def test_chat_latency_is_not_blocked_by_email(self, s):
        # Cached grounded query — expect < 3s round-trip even though prior tests filled throttle state
        start = time.time()
        r = s.post(f"{API}/chat", json={"message": "HIMT contact number", "session_id": str(uuid.uuid4())}, timeout=TIMEOUT)
        elapsed = time.time() - start
        assert r.status_code == 200
        # Preview URL RTT + FastAPI: cached should be well under 3s
        assert elapsed < 5.0, f"chat latency {elapsed:.2f}s too high — email dispatch may be blocking?"

    # ---------- 7. Env vars loaded correctly (verified via test-alert response echo) ----------
    def test_env_vars_loaded_correctly(self, s, admin_headers):
        # This second /admin/test-alert bypasses throttle (endpoint resets _last_alert_ts to 0).
        # We already sent one email at the start of this class; sending a second is acceptable
        # (spec allows 2-3 real sends per run).
        r = s.post(f"{API}/admin/test-alert", headers=admin_headers, timeout=TIMEOUT)
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        # These prove RESEND_FROM_EMAIL and WEAK_ANSWER_ALERT_TO were loaded from .env
        assert d["sent_to"] == WEAK_ANSWER_ALERT_TO
        assert d["from"] == RESEND_FROM_EMAIL
