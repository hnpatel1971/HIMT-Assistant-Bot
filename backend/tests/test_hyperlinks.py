"""Tests for clickable-hyperlink behaviour in answers (Feb 2026).

Every grounded answer should carry at least one URL, and fee/booking answers
should include a `Book online:` link plus a `Learn more:` link. Ideally URLs
are emitted as markdown `[label](url)` so the frontend can render them as
proper clickable anchors.
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

URL_RX = re.compile(r"https?://[^\s()<>\"']+")
MD_LINK_RX = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")


def _post(msg: str, ttl: int = 40) -> dict:
    r = requests.post(CHAT, json={"message": msg}, timeout=ttl)
    r.raise_for_status()
    return r.json()


class TestAnswerHyperlinks:
    """Every grounded answer must contain at least one URL, and fee/booking
    queries must include both a Book online and Learn more link."""

    @pytest.mark.parametrize(
        "q",
        [
            "What are the fees for PSCRB course?",
            "GMDSS course price",
            "What courses does HIMT offer?",
            "HIMT campus location",
            "Where can I download the HIMT app?",
        ],
    )
    def test_answer_has_at_least_one_url(self, q):
        d = _post(q)
        if d["status"] != "grounded":
            pytest.skip(f"query '{q}' returned status={d['status']}")
        assert URL_RX.search(d["answer"]), f"no URL in answer: {d['answer']}"

    def test_fee_answer_has_book_online_and_learn_more(self):
        d = _post("PSCRB fees in Vizag")
        assert d["status"] == "grounded"
        ans_low = d["answer"].lower()
        assert "book online" in ans_low, f"missing 'Book online:' line: {d['answer']}"
        assert "learn more" in ans_low, f"missing 'Learn more:' line: {d['answer']}"
        # And both should contain URLs
        assert URL_RX.search(d["answer"]), "no URLs in fee answer"

    def test_urls_prefer_markdown_link_syntax(self):
        # We ask the same fee question and expect at least one markdown link.
        d = _post("How much does the STCW course cost at HIMT?")
        if d["status"] != "grounded":
            pytest.skip("insufficient")
        assert MD_LINK_RX.search(d["answer"]), (
            f"expected at least one [label](url) markdown link: {d['answer']}"
        )
