"""Tests for the per-course Documents ingestion from himt.co.in.

The booking portal exposes a `Documents` button for each course; the source
of truth is `POST https://ob.himt.co.in/api/V2/GetCourseElig_Details`. The
chatbot must ground its documents-required answer in that data (not in the
generic himtmarine.com FAQ) whenever the user names a specific course.
"""
from __future__ import annotations
import os
import re

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


class TestPerCourseDocsFromHimtCoIn:
    """Named-course docs queries must return grounded lists sourced from himt.co.in."""

    @pytest.mark.parametrize(
        "q,expected_tokens",
        [
            ("Documents required for PSCRB course", ["indos", "medical", "cdc"]),
            ("Documents required for AFF course booking", ["cdc", "medical"]),
            # GMDSS has multiple variants on the booking portal (DGS / UK MCA /
            # Resit). Ask for the DGS one explicitly so the LLM can lock onto
            # a single variant instead of hedging.
            ("Documents required for DGS-approved GMDSS course",
             ["indos", "passport", "photo", "visa", "stamp"]),
        ],
    )
    def test_course_docs_grounded_and_from_himt_co_in(self, q, expected_tokens):
        d = _post(q)
        assert d["status"] == "grounded", (
            f"expected grounded for '{q}': {d['answer'][:300]}"
        )
        ans_low = d["answer"].lower()
        hits = sum(1 for t in expected_tokens if t in ans_low)
        assert hits >= 2, (
            f"expected >=2 of {expected_tokens} in docs answer (found {hits}): {d['answer']}"
        )
        joined_srcs = " ".join(s["url"].lower() for s in d.get("sources", []))
        assert "himt.co.in" in joined_srcs, (
            f"docs answer must cite himt.co.in (the source of truth for per-course docs). "
            f"sources={joined_srcs}"
        )


class TestGenericDocsQueryStillClarifies:
    """Without a course name, the assistant should ask which course rather than
    dumping a generic list (unchanged behaviour from before the Feb 2026 change)."""

    def test_generic_docs_query_asks_for_course(self):
        d = _post("Documents needed for booking")
        assert d["status"] == "grounded"
        ans_low = d["answer"].lower()
        # Either the classic clarification form, or a broad multi-option answer.
        clarify_hits = any(
            t in ans_low for t in (
                "which course", "pre-sea", "pre sea", "presea", "post-sea",
                "specify the course", "which programme", "which one",
            )
        )
        assert clarify_hits, (
            f"generic docs query should ask a clarifying question about the course: {d['answer']}"
        )
