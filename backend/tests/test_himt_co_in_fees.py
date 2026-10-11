"""Iteration 10 — himt.co.in SPA fee-extraction bug fix + 6-hourly refresh scheduler.

Covers:
  * PSCRB fees query returns grounded numeric fee citing himt.co.in booking portal
  * PSCRB Vizag fee
  * AFF+PSCRB+MFA package fee
  * GMDSS fees
  * Regressions: course list, contact info, booking documents, value-added courses
  * Scheduler: /api/admin/schedule has daily-ingest + himt-co-in-refresh
  * Ingestion: /api/admin/reingest + stats thresholds
  * No third-party hotel number leak
"""
import os
import re
import time
import uuid

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL is missing")
BASE_URL = base_url.rstrip("/")
ADMIN_TOKEN = "himt-admin-2026"
TIMEOUT = 120


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


def ask(client, message, session_id=None):
    payload = {"message": message, "session_id": session_id or f"TEST_{uuid.uuid4()}"}
    r = client.post(f"{BASE_URL}/api/chat", json=payload, timeout=TIMEOUT)
    assert r.status_code == 200, f"chat failed {r.status_code}: {r.text[:400]}"
    d = r.json()
    assert isinstance(d.get("answer"), str) and d["answer"].strip(), "empty answer"
    assert d.get("status") in {"grounded", "insufficient", "fallback", "error"}
    return d


def src_urls(d):
    return " ".join(s.get("url", "") for s in d.get("sources", []))


# ---------------- Bug fix: himt.co.in fee extraction ----------------
class TestPSCRBFees:
    def test_pscrb_fees_grounded_with_numeric_fee(self, client):
        d = ask(client, "What are the fees for PSCRB course?")
        blob = d["answer"]
        assert d["status"] == "grounded", f"status={d['status']} answer={blob[:400]}"
        digits = re.sub(r"[,\s]", "", blob)
        assert any(f in digits for f in ("6900", "8900", "44900")), f"no known fee value in answer: {blob[:500]}"
        assert "himt.co.in" in src_urls(d), f"sources missing himt.co.in: {src_urls(d)}"

    def test_pscrb_vizag_fee(self, client):
        d = ask(client, "PSCRB fees in Vizag")
        blob = d["answer"]
        assert d["status"] == "grounded", f"status={d['status']} answer={blob[:400]}"
        assert re.search(r"vizag|visakh", blob, re.I), f"Vizag not mentioned: {blob[:400]}"
        assert "8900" in re.sub(r"[,\s]", "", blob), f"Vizag fee 8,900 missing: {blob[:500]}"

    def test_package_aff_pscrb_mfa_fee(self, client):
        d = ask(client, "What is the fee for the AFF+PSCRB+MFA package?")
        blob = d["answer"]
        assert d["status"] == "grounded", f"status={d['status']} answer={blob[:400]}"
        # Two legitimate AFF+PSCRB+MFA packages exist on the booking portal:
        # DGS Approved bundle (INR 17,200) and UK MCA Approved bundle (INR 131,700).
        # Either is a correct answer unless the user narrows the query.
        flat = re.sub(r"[,\s]", "", blob)
        assert ("17200" in flat) or ("131700" in flat), (
            f"neither DGS (17,200) nor UK MCA (131,700) package fee present: {blob[:500]}"
        )
        assert "himt.co.in" in src_urls(d), f"sources missing himt.co.in: {src_urls(d)}"

    def test_gmdss_fees(self, client):
        d = ask(client, "Fees for GMDSS course")
        blob = d["answer"]
        assert d["status"] == "grounded", f"status={d['status']} answer={blob[:400]}"
        assert re.search(r"(?:INR|Rs\.?|₹)\s?[\d,]{4,}|\b\d{2},\d{3}\b", blob), f"no fee amount: {blob[:500]}"

    def test_no_disconnected_fees_labels(self, client):
        d = ask(client, "What are the fees for PSCRB course?")
        assert not re.search(r"Fees\s+Fees\s+Fees", d["answer"], re.I), "regression: disconnected 'Fees Fees Fees'"


# ---------------- Regressions ----------------
class TestRegressions:
    def test_courses_offered_includes_himtmarine_source(self, client):
        d = ask(client, "What courses does HIMT offer?")
        assert d["status"] == "grounded", f"status={d['status']} answer={d['answer'][:300]}"
        assert "himtmarine.com" in src_urls(d), f"no himtmarine.com source: {src_urls(d)}"

    def test_contact_number_and_email(self, client):
        d = ask(client, "HIMT contact number and email")
        blob = d["answer"]
        assert d["status"] == "grounded", f"status={d['status']} answer={blob[:300]}"
        assert re.search(r"@(himtmarine\.com|himtcollege\.com|himtoffshore\.com|himt\.co\.in)", blob), \
            f"no HIMT email: {blob[:500]}"
        assert re.search(r"(?:\+91[\s-]?)?\d[\d\s\-()]{7,}\d", blob), f"no phone number: {blob[:500]}"

    def test_documents_needed_for_booking(self, client):
        d = ask(client, "Documents needed for booking")
        blob = d["answer"]
        assert d["status"] == "grounded", f"status={d['status']} answer={blob[:400]}"
        assert re.search(r"passport|mark sheet|marksheet|aadhaar|photo|indos|cdc|which course|clarify", blob, re.I), \
            f"neither documents nor clarifying question: {blob[:500]}"

    def test_value_added_courses_cites_page(self, client):
        d = ask(client, "What are the value-added courses?")
        assert d["status"] == "grounded", f"status={d['status']} answer={d['answer'][:300]}"
        assert "value-added-courses" in src_urls(d), f"sources: {src_urls(d)}"

    def test_no_hotel_number_leak(self, client):
        d = ask(client, "HIMT contact")
        blob = d["answer"] + " " + src_urls(d)
        for bad in ("9840730760", "044-26422297", "26422297", "9789066624", "himt-nearby-accommodations"):
            assert bad not in blob.replace(" ", ""), f"third-party leak '{bad}': {blob[:600]}"


# ---------------- Scheduler ----------------
class TestScheduler:
    def test_schedule_requires_token(self, client):
        r = client.get(f"{BASE_URL}/api/admin/schedule", timeout=30)
        assert r.status_code in (401, 403), f"unprotected: {r.status_code}"

    def test_schedule_has_both_jobs(self, client):
        r = client.get(f"{BASE_URL}/api/admin/schedule",
                       headers={"X-Admin-Token": ADMIN_TOKEN}, timeout=30)
        assert r.status_code == 200, r.text[:300]
        jobs = {j["id"]: j for j in r.json().get("jobs", [])}
        assert "daily-ingest" in jobs and "himt-co-in-refresh" in jobs, f"jobs={list(jobs)}"
        for jid in ("daily-ingest", "himt-co-in-refresh"):
            assert jobs[jid].get("next_run_time"), f"{jid} has no next_run_time"
        assert "*/6" in jobs["himt-co-in-refresh"].get("trigger", ""), \
            f"not 6-hourly: {jobs['himt-co-in-refresh'].get('trigger')}"


# ---------------- Ingestion ----------------
class TestIngestion:
    def test_stats_index_populated(self, client):
        r = client.get(f"{BASE_URL}/api/admin/stats",
                       headers={"X-Admin-Token": ADMIN_TOKEN}, timeout=30)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["pages_indexed"] > 50, d
        assert d["chunks_indexed"] > 500, d

    @pytest.mark.slow
    def test_reingest_and_stats(self, client):
        r = client.post(f"{BASE_URL}/api/admin/reingest",
                        headers={"X-Admin-Token": ADMIN_TOKEN}, timeout=60)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d.get("status") in ("started", "already_running", "throttled") and d.get("run_id"), d
        deadline = time.time() + 180
        stats = None
        while time.time() < deadline:
            time.sleep(20)
            s = client.get(f"{BASE_URL}/api/admin/stats",
                           headers={"X-Admin-Token": ADMIN_TOKEN}, timeout=30)
            stats = s.json()
            if stats["pages_indexed"] > 50 and stats["chunks_indexed"] > 500:
                break
        assert stats and stats["pages_indexed"] > 50 and stats["chunks_indexed"] > 500, stats
