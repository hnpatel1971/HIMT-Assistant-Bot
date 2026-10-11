"""Pre-sea answers must NOT emit a `Book online:` line (Feb 2026).

Pre-sea programmes (DNS, B.Sc / B.Tech, GME, ETO, GP Rating, OCCP, BSm, IMEC)
are admission-based via HIMT College; they do NOT go through the himt.co.in
online booking portal. Showing `Book online: HIMT Online Booking` on those
answers is misleading, so we assert it's absent.
"""
from __future__ import annotations
import os

import pytest
import requests

API_URL = os.environ.get("EXTERNAL_API_URL") or (
    open("/app/frontend/.env").read().split("REACT_APP_BACKEND_URL=")[1].split("\n")[0].strip()
)
CHAT = f"{API_URL}/api/chat"


def _post(msg: str, ttl: int = 40) -> dict:
    r = requests.post(CHAT, json={"message": msg}, timeout=ttl)
    r.raise_for_status()
    return r.json()


class TestPreSeaNoBookOnline:
    @pytest.mark.parametrize(
        "q",
        [
            "Tell me about the DNS course",
            "GP Rating course details",
            "B.Sc Nautical Science eligibility",
            "What are the pre-sea programmes at HIMT?",
            "GME course duration",
            "ETO programme at HIMT College",
        ],
    )
    def test_no_book_online_on_presea(self, q):
        d = _post(q)
        if d["status"] != "grounded":
            pytest.skip(f"query '{q}' returned status={d['status']}")
        ans = d["answer"] or ""
        assert "book online" not in ans.lower(), (
            f"pre-sea answer should NOT emit 'Book online:' — pre-sea admissions "
            f"don't go through the booking portal. Query='{q}' answer={ans[:400]}"
        )
        # Should still emit a Learn more: link (or at least SOME URL).
        assert "learn more" in ans.lower() or "http" in ans.lower(), (
            f"answer missing any learn-more URL: {ans}"
        )


class TestPostSeaKeepsBookOnline:
    """Sanity: fee / batch / package answers (post-sea) still keep Book online:."""

    @pytest.mark.parametrize(
        "q",
        [
            "PSCRB course fees",
            "GMDSS course batch dates",
            "AFF+PSCRB+MFA package fee",
        ],
    )
    def test_book_online_present_on_postsea(self, q):
        d = _post(q)
        if d["status"] != "grounded":
            pytest.skip(f"query '{q}' returned status={d['status']}")
        ans_low = (d["answer"] or "").lower()
        assert "book online" in ans_low, (
            f"post-sea fee/batch answer must include 'Book online:'. query='{q}' "
            f"answer={d['answer'][:400]}"
        )
