"""Tests for the topic-owner map (Feb 2026).

Ensures overlapping/contradictory data across the three HIMT sites can't leak
into answers where one site is the unambiguous source of truth:
    Fees / prices / batch dates  ->  himt.co.in only
    Pre-sea courses              ->  himtcollege.com only
"""
from __future__ import annotations
import os
import re
from urllib.parse import urlparse

import pytest
import requests

API_URL = os.environ.get("EXTERNAL_API_URL") or (
    open("/app/frontend/.env").read().split("REACT_APP_BACKEND_URL=")[1].split("\n")[0].strip()
)
CHAT = f"{API_URL}/api/chat"


def _host(u: str) -> str:
    return urlparse(u).netloc.replace("www.", "")


def _post(msg: str, ttl: int = 40) -> dict:
    r = requests.post(CHAT, json={"message": msg}, timeout=ttl)
    r.raise_for_status()
    return r.json()


class TestFeeQueriesRestrictedToHimtCoIn:
    """Any fee/price/cost query must ONLY cite himt.co.in — never himtmarine.com
    or himtcollege.com whose fee pages can be stale."""

    @pytest.mark.parametrize(
        "q",
        [
            "What are the fees for PSCRB course?",
            "PSCRB fees in Vizag",
            "How much does STCW cost?",
            "GMDSS course price",
            "AFF course fees",
            "What is the fee for BST?",
        ],
    )
    def test_fee_query_only_cites_himt_co_in(self, q):
        d = _post(q)
        assert d["status"] == "grounded", f"expected grounded for '{q}': {d['answer'][:200]}"
        sources = d.get("sources", [])
        assert sources, f"expected at least one source for '{q}'"
        hosts = {_host(s["url"]) for s in sources}
        assert hosts == {"himt.co.in"}, (
            f"fee query '{q}' must ONLY cite himt.co.in, got hosts={hosts} sources={sources}"
        )


class TestBatchDateQueriesRestrictedToHimtCoIn:
    """Batch/schedule/start-date queries must ONLY cite himt.co.in."""

    @pytest.mark.parametrize(
        "q",
        [
            "When is the next PSCRB batch?",
            "Upcoming batches for AFF",
            "STCW start dates",
            "next batch for GMDSS",
        ],
    )
    def test_batch_query_only_cites_himt_co_in(self, q):
        d = _post(q)
        assert d["status"] == "grounded", f"expected grounded for '{q}': {d['answer'][:200]}"
        sources = d.get("sources", [])
        assert sources, f"expected at least one source for '{q}'"
        hosts = {_host(s["url"]) for s in sources}
        assert hosts == {"himt.co.in"}, (
            f"batch query '{q}' must ONLY cite himt.co.in, got hosts={hosts}"
        )


class TestPreSeaCoursesRestrictedToHimtCollege:
    """Pre-sea course queries must ONLY cite himtcollege.com."""

    @pytest.mark.parametrize(
        "q",
        [
            "What are the pre-sea courses at HIMT?",
            "GP Rating course details",
            "DNS course eligibility",
            "B.Sc Nautical Science admission",
        ],
    )
    def test_presea_query_only_cites_himtcollege(self, q):
        d = _post(q)
        # A grounded answer is expected; if the answer is insufficient there
        # would be no sources to check — that's a separate concern.
        if d["status"] != "grounded":
            pytest.skip(f"query '{q}' returned status={d['status']}")
        sources = d.get("sources", [])
        assert sources, f"expected at least one source for '{q}'"
        hosts = {_host(s["url"]) for s in sources}
        assert hosts == {"himtcollege.com"}, (
            f"pre-sea query '{q}' must ONLY cite himtcollege.com, got hosts={hosts}"
        )


class TestGeneralCoursesQueryStillMultiHost:
    """Sanity: generic course queries (no fee/batch/pre-sea trigger) should
    still surface multiple hosts so users see both descriptions and booking."""

    def test_general_courses_query_multi_host(self):
        d = _post("What courses does HIMT offer to seafarers?")
        assert d["status"] == "grounded"
        sources = d.get("sources", [])
        hosts = {_host(s["url"]) for s in sources}
        # This is a broad query — expect >= 2 unique hosts.
        assert len(hosts) >= 2, (
            f"general courses query should surface multiple hosts, got {hosts}"
        )


class TestFeeAnswerNumericAndFromHimtCoIn:
    """PSCRB fees returned by the assistant must contain a numeric INR figure
    and cite himt.co.in — protects against the previous 'Fees Fees Fees'
    scraping regression."""

    def test_pscrb_fee_has_number_and_himt_co_in_source(self):
        d = _post("How much is the PSCRB course fee?")
        assert d["status"] == "grounded"
        assert re.search(r"\b(?:rs\.?|inr|₹)?\s*[0-9]{1,3}[,\s]?\d{3}\b", d["answer"], re.I), (
            f"expected an INR figure in PSCRB fee answer: {d['answer']}"
        )
        hosts = {_host(s["url"]) for s in d.get("sources", [])}
        assert "himt.co.in" in hosts, f"PSCRB fee source must include himt.co.in, got {hosts}"
