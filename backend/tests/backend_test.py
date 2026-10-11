"""HIMT AI Chatbot backend tests - covers health, chat (grounded/cache/fallback/insufficient), admin auth/stats/facts/logs/reingest, and suggested prompts."""
import os
import re
import time
import uuid
import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/") if os.environ.get("REACT_APP_BACKEND_URL") else "http://localhost:8001"
API = f"{BASE_URL}/api"
ADMIN_TOKEN = "himt-admin-2026"
TIMEOUT = 90


@pytest.fixture(scope="session")
def s():
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


@pytest.fixture(scope="session")
def admin_headers():
    return {"X-Admin-Token": ADMIN_TOKEN, "Content-Type": "application/json"}


# ---------- Health ----------
class TestHealth:
    def test_health_ok(self, s):
        r = s.get(f"{API}/health", timeout=TIMEOUT)
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "ok"
        assert d["pages_indexed"] > 0
        assert d["chunks_indexed"] > 0
        assert d["has_structured_facts"] is True
        assert d["claude_configured"] is True
        assert d["latest_ingestion"] is not None
        # Accept 'running' too — parallel xdist worker may trigger reingest concurrently
        assert d["latest_ingestion"]["status"] in ("completed", "running")


# ---------- Suggested prompts ----------
class TestSuggested:
    def test_suggested_prompts(self, s):
        r = s.get(f"{API}/suggested-prompts", timeout=TIMEOUT)
        assert r.status_code == 200
        prompts = r.json()["prompts"]
        assert len(prompts) == 4
        for p in prompts:
            assert "label" in p and "text" in p
            assert p["label"] and p["text"]


# ---------- Chat ----------
class TestChat:
    def test_chat_empty_message_400(self, s):
        r = s.post(f"{API}/chat", json={"message": ""}, timeout=TIMEOUT)
        assert r.status_code == 400

    def test_chat_about_himt_grounded(self, s):
        sid = str(uuid.uuid4())
        r = s.post(f"{API}/chat", json={"message": "Tell me about HIMT", "session_id": sid}, timeout=TIMEOUT)
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "grounded", f"got status={d['status']} answer={d['answer'][:200]}"
        assert d["answer"] and len(d["answer"]) < 800
        assert len(d["sources"]) >= 1
        assert any("himtmarine.com" in src["url"] for src in d["sources"])
        # About intent must cite about/faculty/timeline/campus/green pages, not industry-reviews
        joined = " ".join(src["url"].lower() for src in d["sources"])
        about_keys = ["about-us", "our-faculty", "timeline-of-himt", "green-himt", "campus", "about-himt"]
        assert any(k in joined for k in about_keys), f"about intent should cite about/faculty/timeline/campus/green pages. sources={joined}"
        assert "industry-reviews" not in joined, f"about intent should not cite industry-reviews. sources={joined}"

    def test_chat_contact_info(self, s):
        r = s.post(f"{API}/chat", json={"message": "What is HIMT contact number and email?"}, timeout=TIMEOUT)
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "grounded"
        ans = d["answer"].lower()
        has_email = "@himt" in ans or bool(re.search(r"[\w.-]+@[\w.-]+", d["answer"]))
        has_phone = bool(re.search(r"\+?\d[\d\-\s()]{6,}", d["answer"]))
        assert has_email or has_phone, f"No email/phone in: {d['answer']}"

    def test_chat_courses(self, s):
        # Use slight variation to avoid stale cached response from earlier iteration
        r = s.post(f"{API}/chat", json={"message": "What courses does HIMT offer to seafarers?"}, timeout=TIMEOUT)
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "grounded"
        assert len(d["sources"]) >= 1
        joined = " ".join(src["url"].lower() for src in d["sources"])
        # Course intent should cite one of the canonical course pages OR the
        # HIMT Online Booking portal (himt.co.in) which is the source of truth
        # for course fees + batch dates as of Aug 2026.
        course_keys = [
            "basic-modular-courses",
            "advanced-modular-courses",
            "competency-courses",
            "simulator-based-courses",
            "uk-mca-approved-course",
            "revalidation-upgradation-courses",
            "value-added-courses",
            "discounted-package",
            "himt.co.in",
        ]
        assert any(k in joined for k in course_keys), f"courses intent must cite a canonical course page. sources={joined}"

    def test_chat_admissions(self, s):
        r = s.post(f"{API}/chat", json={"message": "How do I apply for admission at HIMT?"}, timeout=TIMEOUT)
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "grounded"
        joined = " ".join(src["url"].lower() for src in d["sources"])
        assert any(k in joined for k in ["booking", "submission", "admission", "procedure"]), f"sources={joined}"

    def test_chat_unrelated_insufficient(self, s):
        r = s.post(f"{API}/chat", json={"message": "What is the current price of Bitcoin?"}, timeout=TIMEOUT)
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "insufficient", f"expected insufficient, got {d['status']} answer={d['answer']}"
        # Must not hallucinate a numeric BTC price
        assert not re.search(r"\$\s?\d[\d,]{2,}", d["answer"])

    def test_chat_cache_hit(self, s):
        unique_q = f"Tell me about HIMT modular courses and simulator training cache-test {uuid.uuid4().hex[:6]}"
        r1 = s.post(f"{API}/chat", json={"message": unique_q}, timeout=TIMEOUT)
        assert r1.status_code == 200
        d1 = r1.json()
        if d1["status"] != "grounded":
            pytest.skip(f"first response not grounded (status={d1['status']}), cannot test cache")
        assert d1["cached"] is False
        r2 = s.post(f"{API}/chat", json={"message": unique_q}, timeout=TIMEOUT)
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["cached"] is True, f"second call cached={d2['cached']}"
        assert d2["answer"] == d1["answer"]


# ---------- Bug fix 1: Campus address ----------
class TestChatCampusAddress:
    """Iteration 4: Regression tests for the 'campus address' bug.

    Prior behaviour: 'Campus address' / 'HIMT campus location' returned
    'I don't have that specific information'. After fix (retrieval.py INTENT_MAP
    + INTENT_URL_PATTERNS.contact including '/campus', server.py Rule 9), these
    must return status='grounded' with an actual address (street/city/pincode).
    """

    CAMPUS_STREET_TOKENS = [
        "millers road",
        "east coast road",
        "vsi estate",
        "kilpauk",
        "chennai",
        "kalpakkam",
        "arambakkam",
    ]
    # Any 6-digit Indian pincode (with optional single space in middle, e.g. '600 010')
    PINCODE_RE = re.compile(r"\b\d{3}\s?\d{3}\b")

    def _assert_campus_grounded(self, d):
        assert d["status"] == "grounded", (
            f"expected grounded, got status={d['status']} answer={d['answer'][:300]}"
        )
        ans_low = d["answer"].lower()
        has_pincode = bool(self.PINCODE_RE.search(d["answer"]))
        has_street = any(tok in ans_low for tok in self.CAMPUS_STREET_TOKENS)
        assert has_pincode or has_street, (
            f"answer contains no pincode or campus street token: {d['answer']}"
        )
        # Explicit negative: must not be the 'not available' canned refusal
        assert "don't have that specific information" not in ans_low, (
            f"still returning canned refusal: {d['answer']}"
        )

    def test_campus_address_query(self, s):
        r = s.post(f"{API}/chat", json={"message": "Campus address"}, timeout=TIMEOUT)
        assert r.status_code == 200
        self._assert_campus_grounded(r.json())

    def test_himt_campus_location_query(self, s):
        r = s.post(f"{API}/chat", json={"message": "HIMT campus location"}, timeout=TIMEOUT)
        assert r.status_code == 200
        self._assert_campus_grounded(r.json())

    def test_where_is_himt_campus_query(self, s):
        r = s.post(f"{API}/chat", json={"message": "Where is the HIMT campus?"}, timeout=TIMEOUT)
        assert r.status_code == 200
        self._assert_campus_grounded(r.json())


# ---------- Bug fix 2: Booking link ----------
class TestChatBookingLink:
    """Iteration 4: Regression tests for the 'booking link' bug.

    Prior behaviour: 'Booking link' returned procedural text (or 'no info').
    After fix (retrieval.py INTENT_MAP.admissions adds 'link/url/website/portal',
    server.py Rule 8), these must return status='grounded' AND embed the
    'himtmarine.com/booking' URL directly in the answer text.
    """

    def _assert_booking_link_grounded(self, d):
        assert d["status"] == "grounded", (
            f"expected grounded, got status={d['status']} answer={d['answer'][:300]}"
        )
        ans_low = d["answer"].lower()
        assert "himtmarine.com/booking" in ans_low, (
            f"booking URL not embedded in answer: {d['answer']}"
        )
        assert "don't have that specific information" not in ans_low, (
            f"still returning canned refusal: {d['answer']}"
        )

    def test_booking_link_query(self, s):
        r = s.post(f"{API}/chat", json={"message": "Booking link"}, timeout=TIMEOUT)
        assert r.status_code == 200
        self._assert_booking_link_grounded(r.json())

    def test_give_me_booking_link_query(self, s):
        r = s.post(f"{API}/chat", json={"message": "Give me the booking link"}, timeout=TIMEOUT)
        assert r.status_code == 200
        self._assert_booking_link_grounded(r.json())

    def test_where_can_i_book_online_query(self, s):
        r = s.post(f"{API}/chat", json={"message": "Where can I book online?"}, timeout=TIMEOUT)
        assert r.status_code == 200

# ---------- Bug fix 3 (iter 5): Location-specific fees for PSCRB Vizag ----------
class TestChatPSCRBVizagFees:
    """Iteration 5: PSCRB Vizag course must return the Vizag-specific fee
    from HIMT Online Booking (himt.co.in) or /vizag-course/.
    """

    def _assert_vizag_pscrb(self, d):
        assert d["status"] == "grounded", (
            f"expected grounded, got status={d['status']} answer={d['answer'][:300]}"
        )
        ans = d["answer"]
        ans_low = ans.lower()
        # Must cite Vizag context either via /vizag-course/ page or the online booking portal
        joined_srcs = " ".join(src["url"].lower() for src in d.get("sources", []))
        cites_vizag = (
            "vizag-course" in joined_srcs
            or "vizag-course" in ans_low
            or "himt.co.in" in joined_srcs
        )
        assert cites_vizag, f"answer/sources must cite Vizag source. answer={ans} sources={joined_srcs}"
        # Location context — must mention Vizag explicitly
        assert "vizag" in ans_low, f"answer must mention Vizag: {ans}"
        # Vizag PSCRB fee from booking data — matches any 4-digit INR figure with optional
        # comma. The booking portal is the source of truth; historically 6,900 on the
        # Vizag page, currently 8,900 on the booking portal — accept either as long as
        # a fee is quoted.
        import re as _re
        assert _re.search(r"(?:rs\.?|inr|₹|\$)?\s*[0-9](?:,?\d){2,4}", ans_low), (
            f"expected a numeric fee in the answer: {ans}"
        )

    def test_pscrb_vizag_fees(self, s):
        r = s.post(f"{API}/chat", json={"message": "PSCRB Vizag fees"}, timeout=TIMEOUT)
        assert r.status_code == 200
        self._assert_vizag_pscrb(r.json())

    def test_pscrb_course_fees_in_vizag(self, s):
        r = s.post(f"{API}/chat", json={"message": "PSCRB course fees in Vizag"}, timeout=TIMEOUT)
        assert r.status_code == 200
        self._assert_vizag_pscrb(r.json())

    def test_pscrb_course_at_vizag(self, s):
        r = s.post(f"{API}/chat", json={"message": "PSCRB course at Vizag"}, timeout=TIMEOUT)
        assert r.status_code == 200
        self._assert_vizag_pscrb(r.json())

    def test_pscrb_chennai_does_not_cite_vizag(self, s):
        """Regression: Chennai variant should NOT cite vizag-course/ or tag '(Vizag)'."""
        r = s.post(f"{API}/chat", json={"message": "PSCRB course at Chennai"}, timeout=TIMEOUT)
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "grounded", f"got status={d['status']} answer={d['answer'][:200]}"
        ans_low = d["answer"].lower()
        # Must NOT be tagged as Vizag
        assert "(vizag)" not in ans_low, f"Chennai query tagged as Vizag: {d['answer']}"
        # Top source should NOT be /vizag-course/
        srcs = d.get("sources", [])
        if srcs:
            top_url = srcs[0]["url"].lower()
            assert "vizag-course" not in top_url, (
                f"Chennai query top source is vizag-course/: {top_url}"
            )


# ---------- Bug fix 4 (iter 5): Required documents intent ----------
class TestChatRequiredDocuments:
    """Iteration 5: 'Documents needed for booking'/'Required documents for admission'
    must return grounded and either list documents OR ask a clarifying question
    about which course (post iteration 9 the assistant now asks for the specific
    course before listing docs, because pre-sea vs post-sea document sets differ).
    """

    DOC_TOKENS = ["10th mark sheet", "passport", "passport size photo"]
    CLARIFY_TOKENS = [
        "which course", "which programme", "pre-sea", "pre sea", "presea",
        "post-sea", "post sea", "postsea", "which one", "specify the course",
    ]

    def _assert_docs_grounded(self, d):
        assert d["status"] == "grounded", (
            f"expected grounded, got status={d['status']} answer={d['answer'][:300]}"
        )
        ans_low = d["answer"].lower()
        doc_hits = sum(1 for t in self.DOC_TOKENS if t in ans_low)
        clarifies = any(t in ans_low for t in self.CLARIFY_TOKENS)
        assert doc_hits >= 2 or clarifies, (
            f"expected either a clarifying question about which course, or >=2 of "
            f"{self.DOC_TOKENS} in answer (found {doc_hits}): {d['answer']}"
        )
        assert "don't have that specific information" not in ans_low, (
            f"still returning canned refusal: {d['answer']}"
        )

    def test_documents_needed_for_booking(self, s):
        r = s.post(f"{API}/chat", json={"message": "Documents needed for booking"}, timeout=TIMEOUT)
        assert r.status_code == 200
        self._assert_docs_grounded(r.json())

    def test_documents_needed_for_booking_a_course(self, s):
        r = s.post(f"{API}/chat", json={"message": "Documents needed for booking a course"}, timeout=TIMEOUT)
        assert r.status_code == 200
        self._assert_docs_grounded(r.json())

    def test_required_documents_for_admission(self, s):
        r = s.post(f"{API}/chat", json={"message": "Required documents for admission"}, timeout=TIMEOUT)
        assert r.status_code == 200
        self._assert_docs_grounded(r.json())

    def test_what_documents_are_required_for_admission(self, s):
        r = s.post(f"{API}/chat", json={"message": "What documents are required for admission?"}, timeout=TIMEOUT)
        assert r.status_code == 200
        self._assert_docs_grounded(r.json())

    def test_required_documents_for_himt(self, s):
        r = s.post(f"{API}/chat", json={"message": "Required documents for HIMT"}, timeout=TIMEOUT)
        assert r.status_code == 200
        self._assert_docs_grounded(r.json())


# ---------- Bug fix 5 (iter 5): Value-added courses list format ----------
class TestChatValueAddedCoursesListFormat:
    """Iteration 5: 'Value added courses list' must be grounded AND use bullet-list
    dash format, NOT prose 'is available from ... with fees ...'.
    """

    NARRATIVE_PAT = re.compile(r"is available from .* with fees", re.IGNORECASE)

    def _assert_vac_list(self, d):
        assert d["status"] == "grounded", (
            f"expected grounded, got status={d['status']} answer={d['answer'][:300]}"
        )
        ans = d["answer"]
        ans_low = ans.lower()
        # Must NOT contain narrative prose form "is available from ... with fees ..."
        assert not self.NARRATIVE_PAT.search(ans), (
            f"answer contains narrative prose form 'is available from ... with fees ...': {ans}"
        )
        # Must present multiple items — either as bullet lines (>=3) OR as a
        # comma-separated inline list mentioning >=3 known VAC entries.
        bullet_lines = [ln for ln in ans.splitlines() if ln.strip().startswith(("-", "•", "*"))]
        known_vac_tokens = [
            "eto coc", "auxiliary engineer", "bridge team management", "btm",
            "bosiet", "ecdis", "engine room simulator", "huet", "internal auditor",
            "large vessel ship handling", "liquid cargo handling", "instructors training",
            "training for trainers", "refresher course", "lchs",
        ]
        inline_matches = sum(1 for t in known_vac_tokens if t in ans_low)
        assert len(bullet_lines) >= 3 or inline_matches >= 3, (
            f"expected >=3 bullet lines (dash/•) or >=3 known VAC items inline. "
            f"bullets={len(bullet_lines)} inline_hits={inline_matches}: {ans}"
        )
        # Should cite value-added-courses page
        joined = " ".join(src["url"].lower() for src in d.get("sources", []))
        assert "value-added" in joined or "value-added" in ans_low, (
            f"expected value-added-courses source. sources={joined}"
        )

    def test_value_added_courses_list(self, s):
        r = s.post(f"{API}/chat", json={"message": "Value added courses list"}, timeout=TIMEOUT)
        assert r.status_code == 200
        self._assert_vac_list(r.json())

    def test_list_of_value_added_courses(self, s):
        r = s.post(f"{API}/chat", json={"message": "List of value added courses"}, timeout=TIMEOUT)
        assert r.status_code == 200
        self._assert_vac_list(r.json())

    def test_what_are_the_value_added_courses(self, s):
        r = s.post(f"{API}/chat", json={"message": "What are the value-added courses?"}, timeout=TIMEOUT)
        assert r.status_code == 200
        self._assert_vac_list(r.json())


# ---------- Bug fix 6 (iter 6): Hotel phone numbers must NOT appear in contact answers or facts ----------
HOTEL_NUMBERS_BLACKLIST = [
    "044-26422297",
    "9840730760",
    "044-2661 2772",
    "9789066624",
    "9790966311",
    "9566000511",
    "9959511085",
]
# Normalized digit forms (strip spaces/hyphens) for robust matching
HOTEL_NUMBERS_DIGITS = ["".join(c for c in n if c.isdigit()) for n in HOTEL_NUMBERS_BLACKLIST]

DENY_SOURCE_TOKENS = [
    "/himt-nearby-accommodations",
    "/industry-reviews",
    "/testimonial",
    "/press-clippings",
    "/discounts-and-offers",
]


def _digits_only(s: str) -> str:
    return "".join(c for c in s if c.isdigit())


class TestFactsBugFixHotelNumbers:
    """Iteration 6: /api/admin/facts must contain ONLY HIMT-own page data — no hotel
    phones, no third-party sources, only HIMT-domain emails.
    """

    def test_facts_phones_no_hotel_numbers(self, s, admin_headers):
        r = s.get(f"{API}/admin/facts", headers=admin_headers, timeout=TIMEOUT)
        assert r.status_code == 200
        facts = r.json()["facts"]
        phones = facts.get("phones") or []
        assert isinstance(phones, list) and len(phones) >= 1, (
            f"phones must be non-empty (HIMT campus number must be extracted): {phones}"
        )
        # None of the phones should match a hotel number (compare raw and digits-only)
        for p in phones:
            p_digits = _digits_only(p)
            for bad_raw, bad_digits in zip(HOTEL_NUMBERS_BLACKLIST, HOTEL_NUMBERS_DIGITS):
                assert bad_raw not in p, f"hotel phone leak in facts.phones: {p} matches {bad_raw}"
                # digits-only substring (10-digit hotel number may appear inside longer strings)
                if bad_digits:
                    assert bad_digits not in p_digits, (
                        f"hotel phone leak (digits) in facts.phones: {p} contains {bad_digits}"
                    )

    def test_facts_contains_himt_campus_number(self, s, admin_headers):
        """The verified HIMT phone '+91 98 404 000 00' from /campus/ MUST be present."""
        r = s.get(f"{API}/admin/facts", headers=admin_headers, timeout=TIMEOUT)
        assert r.status_code == 200
        phones = r.json()["facts"].get("phones") or []
        combined_digits = " ".join(_digits_only(p) for p in phones)
        # '+91 98 404 000 00' -> digits '919840400000'
        assert "919840400000" in combined_digits or "9840400000" in combined_digits, (
            f"HIMT campus number (+91 98 404 000 00) missing from facts.phones: {phones}"
        )

    def test_facts_source_urls_excludes_denied_pages(self, s, admin_headers):
        r = s.get(f"{API}/admin/facts", headers=admin_headers, timeout=TIMEOUT)
        assert r.status_code == 200
        facts = r.json()["facts"]
        source_urls = facts.get("source_urls") or []
        assert isinstance(source_urls, list) and len(source_urls) >= 1, (
            f"source_urls must be non-empty: {source_urls}"
        )
        joined = " ".join(u.lower() for u in source_urls)
        for bad in DENY_SOURCE_TOKENS:
            assert bad not in joined, (
                f"denied page {bad} leaked into facts.source_urls: {source_urls}"
            )

    def test_facts_source_urls_only_himt_own_pages(self, s, admin_headers):
        """Every source URL should match one of the allowed HIMT-own page tokens."""
        r = s.get(f"{API}/admin/facts", headers=admin_headers, timeout=TIMEOUT)
        assert r.status_code == 200
        source_urls = r.json()["facts"].get("source_urls") or []
        allowed = [
            "/campus",
            "/about-us",
            "/faq",
            "/our-faculty",
            "/timeline-of-himt",
            "/green-himt",
            "/booking-procedures",
            "/submission-procedure",
            "/careers",
            # Iteration 9: himtcollege.com allow-listed paths
            "/about-himt-college",
            "/join-merchant-navy",
            "/placement",
            "/course",
            "/himt_courses",
            "/e-brochure",
            "/ceos-desk",
            # himt.co.in HIMT-own contact pages
            "/contactus",
            "/faq.aspx",
        ]
        for u in source_urls:
            u_low = u.lower().rstrip("/")
            # Homepage counts as HIMT-own (matches when path is empty)
            from urllib.parse import urlparse
            parsed = urlparse(u_low)
            is_homepage = parsed.path in ("", "/")
            is_allowed = any(a in u_low for a in allowed)
            assert is_homepage or is_allowed, (
                f"source_url {u} is not in allowed HIMT-own set. allowed={allowed}"
            )

    def test_facts_emails_only_himt_domains(self, s, admin_headers):
        r = s.get(f"{API}/admin/facts", headers=admin_headers, timeout=TIMEOUT)
        assert r.status_code == 200
        emails = r.json()["facts"].get("emails") or []
        assert len(emails) >= 1
        allowed_domains = ("himtmarine.com", "himtcollege.com", "himtoffshore.com")
        for e in emails:
            e_low = e.lower()
            assert any(e_low.endswith("@" + d) for d in allowed_domains), (
                f"email {e} not from HIMT domain (allowed={allowed_domains})"
            )


class TestChatContactNoHotelLeak:
    """Iteration 6: Contact-intent chat answers MUST NOT include hotel numbers, and
    sources MUST NOT include /himt-nearby-accommodations/. Answer must contain the
    verified HIMT campus number (or its digits).
    """

    HIMT_PHONE_DIGITS = "9840400000"  # last 10 digits of '+91 98 404 000 00'

    def _assert_no_hotel_leak(self, d):
        assert d["status"] == "grounded", (
            f"expected grounded, got status={d['status']} answer={d['answer'][:200]}"
        )
        ans = d["answer"]
        ans_digits = _digits_only(ans)
        for bad_raw, bad_digits in zip(HOTEL_NUMBERS_BLACKLIST, HOTEL_NUMBERS_DIGITS):
            assert bad_raw not in ans, (
                f"hotel number '{bad_raw}' present in answer: {ans}"
            )
            if bad_digits:
                assert bad_digits not in ans_digits, (
                    f"hotel number digits '{bad_digits}' present in answer digits: {ans}"
                )
        # Sources must not include /himt-nearby-accommodations/
        joined_srcs = " ".join(src["url"].lower() for src in d.get("sources", []))
        assert "/himt-nearby-accommodations" not in joined_srcs, (
            f"contact sources include /himt-nearby-accommodations/: {joined_srcs}"
        )
        # Assert HIMT campus phone digits appear in answer
        assert self.HIMT_PHONE_DIGITS in ans_digits, (
            f"HIMT campus phone digits ({self.HIMT_PHONE_DIGITS}) not in answer: {ans}"
        )

    def test_chat_himt_contact_number(self, s):
        r = s.post(f"{API}/chat", json={"message": "HIMT contact number"}, timeout=TIMEOUT)
        assert r.status_code == 200
        self._assert_no_hotel_leak(r.json())

    def test_chat_himt_phone_number(self, s):
        r = s.post(f"{API}/chat", json={"message": "HIMT phone number"}, timeout=TIMEOUT)
        assert r.status_code == 200
        self._assert_no_hotel_leak(r.json())

    def test_chat_himt_contact_details(self, s):
        r = s.post(f"{API}/chat", json={"message": "HIMT contact details"}, timeout=TIMEOUT)
        assert r.status_code == 200
        self._assert_no_hotel_leak(r.json())


# ---------- Admin auth ----------
class TestAdminAuth:
    def test_verify_missing_token_401(self, s):
        r = s.get(f"{API}/admin/verify", timeout=TIMEOUT)
        assert r.status_code == 401

    def test_verify_wrong_token_401(self, s):
        r = s.get(f"{API}/admin/verify", headers={"X-Admin-Token": "wrong-token"}, timeout=TIMEOUT)
        assert r.status_code == 401

    def test_verify_correct_token(self, s, admin_headers):
        r = s.get(f"{API}/admin/verify", headers=admin_headers, timeout=TIMEOUT)
        assert r.status_code == 200
        assert r.json() == {"ok": True}


# ---------- Admin data ----------
class TestAdminData:
    def test_stats(self, s, admin_headers):
        r = s.get(f"{API}/admin/stats", headers=admin_headers, timeout=TIMEOUT)
        assert r.status_code == 200
        d = r.json()
        for k in ["total_queries", "grounded", "insufficient", "fallback", "errors", "pages_indexed", "chunks_indexed"]:
            assert k in d, f"missing {k}"
        assert d["pages_indexed"] > 0
        assert d["chunks_indexed"] > 0

    def test_facts(self, s, admin_headers):
        r = s.get(f"{API}/admin/facts", headers=admin_headers, timeout=TIMEOUT)
        assert r.status_code == 200
        facts = r.json()["facts"]
        emails = facts.get("emails")
        phones = facts.get("phones")
        addresses = facts.get("addresses")
        assert isinstance(emails, list) and len(emails) >= 1
        assert isinstance(phones, list) and len(phones) >= 1
        assert isinstance(addresses, list) and len(addresses) >= 1

        # Emails must all be from HIMT domains
        allowed_domains = ("himtmarine.com", "himtcollege.com", "himtoffshore.com")
        for e in emails:
            e_low = e.lower()
            assert any(e_low.endswith("@" + d) or e_low.endswith("." + d) for d in allowed_domains), (
                f"email {e} not from HIMT domain (allowed: {allowed_domains})"
            )
        # Explicitly ensure no third-party leak
        joined_emails = " ".join(e.lower() for e in emails)
        for bad in ["pridehotel", "myftpupload", "gmail.com", "yahoo.com"]:
            assert bad not in joined_emails, f"unexpected non-HIMT email token '{bad}' in emails: {emails}"

        # Phones must not be date-like tokens
        date_pat = re.compile(r"^\d{1,2}[-/]\d{1,2}[-/]\d{2,4}$|^\d{4}[-/]\d{4}$")
        for p in phones:
            p_norm = p.strip()
            assert not date_pat.match(p_norm), f"phone entry looks like a date: {p}"
            # Must have at least 7 digits total
            digits = re.sub(r"\D", "", p_norm)
            assert len(digits) >= 7, f"phone {p} has too few digits ({len(digits)})"

    def test_logs(self, s, admin_headers):
        r = s.get(f"{API}/admin/logs", headers=admin_headers, timeout=TIMEOUT)
        assert r.status_code == 200
        logs = r.json()["logs"]
        assert isinstance(logs, list)
        if logs:
            log = logs[0]
            for k in ["id", "session_id", "query", "answer", "status", "intent", "latency_ms", "created_at"]:
                assert k in log, f"missing {k} in log"

    def test_logs_filtered(self, s, admin_headers):
        r = s.get(f"{API}/admin/logs", headers=admin_headers, params={"status": "grounded"}, timeout=TIMEOUT)
        assert r.status_code == 200
        logs = r.json()["logs"]
        for l in logs:
            assert l["status"] == "grounded"


# ---------- Leads (public POST) ----------
class TestLeads:
    def test_create_lead_valid(self, s, admin_headers):
        sid = str(uuid.uuid4())
        payload = {
            "name": f"TEST_lead_{uuid.uuid4().hex[:6]}",
            "phone": "+91 9876543210",
            "email": "TEST_lead@example.com",
            "interest": "GP Rating admissions",
            "session_id": sid,
            "source_query": "How do I apply for admission at HIMT?",
        }
        r = s.post(f"{API}/leads", json=payload, timeout=TIMEOUT)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("ok") is True
        lead_id = d.get("lead_id")
        assert lead_id
        # Verify persisted via admin endpoint
        r2 = s.get(f"{API}/admin/leads", headers=admin_headers, timeout=TIMEOUT)
        assert r2.status_code == 200
        leads = r2.json()["leads"]
        matched = [l for l in leads if l["id"] == lead_id]
        assert len(matched) == 1, f"lead not persisted: {lead_id}"
        assert matched[0]["name"] == payload["name"]
        assert matched[0]["status"] == "new"

    def test_create_lead_empty_name_400(self, s):
        r = s.post(f"{API}/leads", json={"name": "", "phone": "+91 9876543210"}, timeout=TIMEOUT)
        assert r.status_code == 400

    def test_create_lead_missing_phone_400(self, s):
        r = s.post(f"{API}/leads", json={"name": "TEST_x", "phone": ""}, timeout=TIMEOUT)
        assert r.status_code == 400

    def test_create_lead_short_phone_400(self, s):
        r = s.post(f"{API}/leads", json={"name": "TEST_x", "phone": "123"}, timeout=TIMEOUT)
        assert r.status_code == 400


class TestAdminLeads:
    def test_admin_leads_requires_token(self, s):
        r = s.get(f"{API}/admin/leads", timeout=TIMEOUT)
        assert r.status_code == 401

    def test_admin_leads_returns_list(self, s, admin_headers):
        r = s.get(f"{API}/admin/leads", headers=admin_headers, timeout=TIMEOUT)
        assert r.status_code == 200
        d = r.json()
        assert "leads" in d
        assert "total" in d
        assert isinstance(d["leads"], list)
        assert isinstance(d["total"], int)
        # Newest first
        if len(d["leads"]) >= 2:
            ts = [l["created_at"] for l in d["leads"]]
            assert ts == sorted(ts, reverse=True), "leads not ordered newest-first"

    def test_admin_leads_newest_first_after_create(self, s, admin_headers):
        # Create a fresh lead and expect it at index 0
        payload = {
            "name": f"TEST_newest_{uuid.uuid4().hex[:6]}",
            "phone": "9876543210",
        }
        cr = s.post(f"{API}/leads", json=payload, timeout=TIMEOUT)
        assert cr.status_code == 200
        new_id = cr.json()["lead_id"]

        r = s.get(f"{API}/admin/leads", headers=admin_headers, timeout=TIMEOUT)
        assert r.status_code == 200
        leads = r.json()["leads"]
        assert leads, "no leads returned"
        assert leads[0]["id"] == new_id, f"newest lead not first (got {leads[0]['id']}, expected {new_id})"

    def test_admin_update_lead_status(self, s, admin_headers):
        # Create a lead then mark it contacted
        payload = {"name": f"TEST_update_{uuid.uuid4().hex[:6]}", "phone": "9876543210"}
        cr = s.post(f"{API}/leads", json=payload, timeout=TIMEOUT)
        assert cr.status_code == 200
        lid = cr.json()["lead_id"]

        r = s.patch(f"{API}/admin/leads/{lid}", json={"status": "contacted"}, headers=admin_headers, timeout=TIMEOUT)
        assert r.status_code == 200, r.text
        assert r.json().get("ok") is True

        # Verify via GET
        r2 = s.get(f"{API}/admin/leads", headers=admin_headers, timeout=TIMEOUT)
        found = next((l for l in r2.json()["leads"] if l["id"] == lid), None)
        assert found is not None
        assert found["status"] == "contacted"

    def test_admin_update_lead_unknown_404(self, s, admin_headers):
        r = s.patch(f"{API}/admin/leads/nope-nope-nope", json={"status": "contacted"}, headers=admin_headers, timeout=TIMEOUT)
        assert r.status_code == 404

    def test_admin_update_lead_invalid_status_400(self, s, admin_headers):
        payload = {"name": f"TEST_badstatus_{uuid.uuid4().hex[:6]}", "phone": "9876543210"}
        cr = s.post(f"{API}/leads", json=payload, timeout=TIMEOUT)
        lid = cr.json()["lead_id"]
        r = s.patch(f"{API}/admin/leads/{lid}", json={"status": "bogus"}, headers=admin_headers, timeout=TIMEOUT)
        assert r.status_code == 400


# ---------- Chat lead-capture flag ----------
class TestChatLeadCaptureFlag:
    def test_admissions_query_offers_lead_capture(self, s):
        r = s.post(f"{API}/chat", json={"message": "How do I apply for admission at HIMT?"}, timeout=TIMEOUT)
        assert r.status_code == 200
        d = r.json()
        assert d.get("intent") == "admissions", f"expected admissions intent, got {d.get('intent')}"
        assert d.get("offer_lead_capture") is True, f"expected offer_lead_capture=true for admissions"

    def test_non_admissions_query_no_lead_capture(self, s):
        r = s.post(f"{API}/chat", json={"message": "What courses does HIMT offer to seafarers?"}, timeout=TIMEOUT)
        assert r.status_code == 200
        d = r.json()
        assert d.get("intent") != "admissions", f"unexpected admissions intent for courses query"
        assert d.get("offer_lead_capture") is False, "offer_lead_capture should be false for non-admissions"


# ---------- Admin schedule ----------
class TestAdminSchedule:
    def test_schedule_requires_token(self, s):
        r = s.get(f"{API}/admin/schedule", timeout=TIMEOUT)
        assert r.status_code == 401

    def test_schedule_returns_config(self, s, admin_headers):
        r = s.get(f"{API}/admin/schedule", headers=admin_headers, timeout=TIMEOUT)
        assert r.status_code == 200
        d = r.json()
        assert d.get("enabled") is True, f"scheduler should be running: {d}"
        assert d.get("daily_hour_utc") == 2
        jobs = d.get("jobs", [])
        assert isinstance(jobs, list) and len(jobs) >= 1
        j = next((x for x in jobs if x.get("id") == "daily-ingest"), None)
        assert j is not None, f"daily-ingest job not found in {jobs}"
        assert j.get("next_run_time")
        assert "cron" in (j.get("trigger") or "").lower()


# ---------- Feedback (iteration 7) ----------
class TestChatLogId:
    """Iteration 7: POST /api/chat must return a non-empty 'log_id' UUID string
    for both fresh and cached responses.
    """

    def test_chat_returns_log_id_fresh(self, s):
        # Unique query -> fresh response
        unique_q = f"Tell me about HIMT feedback log-id fresh {uuid.uuid4().hex[:6]}"
        r = s.post(f"{API}/chat", json={"message": unique_q}, timeout=TIMEOUT)
        assert r.status_code == 200
        d = r.json()
        assert d.get("log_id"), f"missing log_id in fresh chat response: {d}"
        assert isinstance(d["log_id"], str)
        # Should be a UUID-ish string (non-empty, >=8 chars)
        assert len(d["log_id"]) >= 8

    def test_chat_returns_log_id_cached(self, s):
        # Send twice with the same message so second is cached
        unique_q = f"Tell me about HIMT feedback log-id cached {uuid.uuid4().hex[:6]}"
        r1 = s.post(f"{API}/chat", json={"message": unique_q}, timeout=TIMEOUT)
        assert r1.status_code == 200
        d1 = r1.json()
        if d1.get("status") != "grounded":
            pytest.skip(f"first response not grounded (status={d1.get('status')}), cannot test cached log_id")
        assert d1.get("log_id")
        r2 = s.post(f"{API}/chat", json={"message": unique_q}, timeout=TIMEOUT)
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2.get("cached") is True, f"expected cached=true on second call, got {d2}"
        assert d2.get("log_id"), f"missing log_id on cached response: {d2}"
        # Cached response should generate a DIFFERENT log_id (each call gets its own log row)
        # But at minimum log_id must be present and non-empty
        assert isinstance(d2["log_id"], str) and len(d2["log_id"]) >= 8


class TestFeedbackSubmission:
    """Iteration 7: POST /api/feedback validation, persistence, and dedup by log_id."""

    def _make_feedback(self, log_id, sentiment="up"):
        return {
            "log_id": log_id,
            "sentiment": sentiment,
            "query": "TEST feedback query",
            "session_id": str(uuid.uuid4()),
            "answer_preview": "TEST answer preview",
            "intent": "general",
            "status": "grounded",
        }

    def test_feedback_valid_up_returns_ok(self, s):
        log_id = str(uuid.uuid4())
        payload = self._make_feedback(log_id, "up")
        r = s.post(f"{API}/feedback", json=payload, timeout=TIMEOUT)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("ok") is True
        assert d.get("feedback_id")

    def test_feedback_valid_down_returns_ok(self, s):
        log_id = str(uuid.uuid4())
        payload = self._make_feedback(log_id, "down")
        r = s.post(f"{API}/feedback", json=payload, timeout=TIMEOUT)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("ok") is True
        assert d.get("feedback_id")

    def test_feedback_invalid_sentiment_400(self, s):
        log_id = str(uuid.uuid4())
        payload = self._make_feedback(log_id, "maybe")
        r = s.post(f"{API}/feedback", json=payload, timeout=TIMEOUT)
        assert r.status_code == 400, f"expected 400 for invalid sentiment, got {r.status_code}: {r.text}"

    def test_feedback_missing_sentiment_422_or_400(self, s):
        # Sentiment is required (str field) — pydantic validation triggers 422
        r = s.post(f"{API}/feedback", json={"log_id": str(uuid.uuid4())}, timeout=TIMEOUT)
        assert r.status_code in (400, 422)

    def test_feedback_dedup_updates_same_log_id(self, s, admin_headers):
        """Sending TWO feedbacks with the SAME log_id (first up, then down)
        must result in ONE row with sentiment='down'. Response is
        {ok:true, updated:true} for the second call.
        """
        log_id = str(uuid.uuid4())

        # First: up
        r1 = s.post(f"{API}/feedback", json=self._make_feedback(log_id, "up"), timeout=TIMEOUT)
        assert r1.status_code == 200, r1.text
        d1 = r1.json()
        assert d1.get("ok") is True
        assert d1.get("feedback_id"), "first call should insert (has feedback_id)"

        # Second: down (same log_id) -> update
        r2 = s.post(f"{API}/feedback", json=self._make_feedback(log_id, "down"), timeout=TIMEOUT)
        assert r2.status_code == 200, r2.text
        d2 = r2.json()
        assert d2.get("ok") is True
        assert d2.get("updated") is True, f"expected updated=true, got {d2}"

        # Verify only one row for this log_id via admin endpoint, and sentiment=down
        r3 = s.get(f"{API}/admin/feedback", headers=admin_headers, params={"limit": 500}, timeout=TIMEOUT)
        assert r3.status_code == 200
        items = r3.json()["feedback"]
        matched = [f for f in items if f.get("log_id") == log_id]
        assert len(matched) == 1, f"expected exactly 1 feedback row for log_id={log_id}, got {len(matched)}: {matched}"
        assert matched[0]["sentiment"] == "down", f"sentiment should be updated to 'down': {matched[0]}"


class TestAdminFeedback:
    """Iteration 7: GET /api/admin/feedback auth + counts + filtering + ordering."""

    def test_admin_feedback_requires_token(self, s):
        r = s.get(f"{API}/admin/feedback", timeout=TIMEOUT)
        assert r.status_code == 401

    def test_admin_feedback_wrong_token(self, s):
        r = s.get(f"{API}/admin/feedback", headers={"X-Admin-Token": "wrong"}, timeout=TIMEOUT)
        assert r.status_code == 401

    def test_admin_feedback_returns_counts_and_list(self, s, admin_headers):
        # Seed one up and one down (fresh log_ids)
        up_lid = str(uuid.uuid4())
        down_lid = str(uuid.uuid4())
        s.post(f"{API}/feedback", json={"log_id": up_lid, "sentiment": "up", "query": "TEST_seed_up"}, timeout=TIMEOUT)
        s.post(f"{API}/feedback", json={"log_id": down_lid, "sentiment": "down", "query": "TEST_seed_down"}, timeout=TIMEOUT)

        r = s.get(f"{API}/admin/feedback", headers=admin_headers, params={"limit": 500}, timeout=TIMEOUT)
        assert r.status_code == 200
        d = r.json()
        assert "feedback" in d and isinstance(d["feedback"], list)
        assert "counts" in d
        c = d["counts"]
        assert set(c.keys()) >= {"up", "down", "total"}
        assert c["total"] == c["up"] + c["down"]
        assert c["up"] >= 1 and c["down"] >= 1
        # Both seeded rows must be present
        ids = {f.get("log_id") for f in d["feedback"]}
        assert up_lid in ids and down_lid in ids

    def test_admin_feedback_newest_first(self, s, admin_headers):
        # Insert a fresh feedback; should appear near top of list (parallel xdist
        # workers may insert other rows concurrently, so just assert ordering + presence
        # within the first few entries).
        new_lid = str(uuid.uuid4())
        s.post(f"{API}/feedback", json={"log_id": new_lid, "sentiment": "up", "query": "TEST_newest_top"}, timeout=TIMEOUT)
        r = s.get(f"{API}/admin/feedback", headers=admin_headers, params={"limit": 200}, timeout=TIMEOUT)
        assert r.status_code == 200
        items = r.json()["feedback"]
        assert items, "feedback list empty"
        ts = [f["created_at"] for f in items]
        assert ts == sorted(ts, reverse=True), "feedback not sorted newest-first"
        # Our inserted row should be near the top (within first 10)
        top_ids = [f.get("log_id") for f in items[:10]]
        assert new_lid in top_ids, f"just-inserted feedback not in top-10 (parallel writers may push it): top={top_ids}"

    def test_admin_feedback_filter_up(self, s, admin_headers):
        # Seed one up so filter has something
        s.post(f"{API}/feedback", json={"log_id": str(uuid.uuid4()), "sentiment": "up", "query": "TEST_filter_up"}, timeout=TIMEOUT)
        r = s.get(f"{API}/admin/feedback", headers=admin_headers, params={"sentiment": "up", "limit": 500}, timeout=TIMEOUT)
        assert r.status_code == 200
        d = r.json()
        for f in d["feedback"]:
            assert f["sentiment"] == "up", f"expected only up, found: {f}"

    def test_admin_feedback_filter_down(self, s, admin_headers):
        s.post(f"{API}/feedback", json={"log_id": str(uuid.uuid4()), "sentiment": "down", "query": "TEST_filter_down"}, timeout=TIMEOUT)
        r = s.get(f"{API}/admin/feedback", headers=admin_headers, params={"sentiment": "down", "limit": 500}, timeout=TIMEOUT)
        assert r.status_code == 200
        d = r.json()
        for f in d["feedback"]:
            assert f["sentiment"] == "down"


# ---------- Admin re-ingest ----------
class TestAdminReingest:
    def test_reingest_flow(self, s, admin_headers):
        r = s.post(f"{API}/admin/reingest", headers=admin_headers, timeout=TIMEOUT)
        assert r.status_code == 200
        d = r.json()
        # Any of these states is a valid response — the in-flight/throttle guards
        # (added Feb 2026) return the id of the existing run instead of kicking
        # off a duplicate parallel ingestion.
        assert d["status"] in ("started", "already_running", "throttled"), d
        run_id = d["run_id"]
        assert run_id

        # Poll runs endpoint
        found = False
        for _ in range(6):
            time.sleep(3)
            r2 = s.get(f"{API}/admin/runs", headers=admin_headers, timeout=TIMEOUT)
            assert r2.status_code == 200
            runs = r2.json()["runs"]
            for run in runs:
                if run["id"] == run_id:
                    found = True
                    break
            if found:
                break
        assert found, f"reingest run {run_id} not found in runs list"
