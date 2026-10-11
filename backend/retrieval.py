"""Chunk-level retrieval with intent-aware ranking for HIMT chatbot."""
import re
from collections import Counter
from typing import List, Dict, Tuple, Optional, Set

# Institute name aliases
ALIASES = {
    "himt": "hindustan institute of maritime training",
    "hindustan institute of maritime training": "himt",
}

INTENT_MAP = {
    "contact": ["contact", "phone", "email", "address", "reach", "call", "mail", "location", "office", "number", "campus address", "where is", "situated"],
    "admissions": ["admission", "apply", "apply for", "enrol", "enroll", "enrollment", "book", "booking", "submission", "eligibility", "procedure", "how to join", "link", "url", "website", "portal"],
    "presea": ["pre-sea", "pre sea", "presea", "pre-sea course", "gp rating", "gp-rating", "dns", "eto", "gme", "nautical", "engineering cadet", "cadet", "merchant navy", "bsm", "imec", "b.sc nautical", "diploma nautical", "join merchant"],
    "courses": ["course", "courses", "training", "program", "programs", "programme", "syllabus", "modular", "competency", "simulator", "mca", "dg shipping", "revalidation", "certificate", "upgradation", "fee", "fees", "duration", "batch", "batch date", "start date", "package", "packages", "combo", "bundle", "aff", "bst", "pscrb", "fpff", "efa", "mfa", "medical first aid", "stsdsd", "gsk", "helm"],
    "about": ["about", "history", "faculty", "who is", "what is", "when", "founded", "established", "timeline", "accreditation", "grade", "excellence"],
    "documents": ["document", "documents", "certificates", "paperwork", "papers", "required to submit", "indos", "cdc", "coc", "passport", "mark sheet", "marksheet", "photo"],
    "app": ["himt app", "app details", "mobile app", "download app", "download the app", "play store", "app store", "android app", "ios app", "iphone app", "google play"],
}

# Location tokens that must decisively bias retrieval towards a specific campus page.
LOCATION_URL_BOOST = {
    "vizag": ["/vizag-course"],
    "visakhapatnam": ["/vizag-course"],
}
# When a location token is present, DE-boost the OTHER-location pages.
LOCATION_URL_PENALTY = {
    "vizag": ["/basic-modular", "/advanced-modular", "/competency-courses", "/simulator-based", "/revalidation-upgradation"],
    "visakhapatnam": ["/basic-modular", "/advanced-modular", "/competency-courses", "/simulator-based", "/revalidation-upgradation"],
}

STOPWORDS = set("""a an the is are was were be been being of in on at for to from by with as it its this that these those and or but if then so do does did doing have has had having i me my mine you your yours we our ours they them their theirs he she it him her his hers what which who whom where when why how""".split())


def _stem(t: str) -> str:
    """Light plural/verb stemming so 'fee' matches 'fees', 'batch' matches
    'batches', 'require' matches 'required'/'requires'. Only touches words of
    length >= 4 to keep short codes (BST, AFF, PST) intact.
    """
    if len(t) < 4:
        return t
    for suf in ("iesˇ",):  # placeholder
        pass
    if t.endswith("ies") and len(t) > 4:
        return t[:-3] + "y"
    if t.endswith("es") and len(t) > 4 and t[-3] in "sxzo":
        return t[:-2]
    if t.endswith("ed") and len(t) > 5:
        return t[:-2]
    if t.endswith("ing") and len(t) > 6:
        return t[:-3]
    if t.endswith("s") and not t.endswith("ss"):
        return t[:-1]
    return t


def _tokenize(text: str) -> List[str]:
    text = text.lower()
    # Expand aliases
    for a, b in ALIASES.items():
        text = re.sub(r"\b" + re.escape(a) + r"\b", f"{a} {b}", text)
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9\-]+", text)
    return [_stem(t) for t in tokens if t not in STOPWORDS and len(t) > 1]


def detect_intent(query: str) -> str:
    q = query.lower()
    scores = {}
    for intent, kws in INTENT_MAP.items():
        score = sum(2 if kw in q else 0 for kw in kws)
        # word-boundary check for short keywords
        for kw in kws:
            if re.search(r"\b" + re.escape(kw) + r"\b", q):
                score += 1
        scores[intent] = score

    # "documents" is a more specific intent — if any documents keyword is a strong hit,
    # prefer it over admissions/contact even on tie.
    doc_words = ("document", "documents", "papers", "indos", "cdc", "certificates", "mark sheet", "marksheet")
    if any(w in q for w in doc_words) and scores.get("documents", 0) > 0:
        return "documents"

    # "presea" is more specific than "courses" — prefer it when pre-sea keywords appear.
    presea_words = ("pre-sea", "pre sea", "presea", "gp rating", "gp-rating", "dns", " eto", "gme",
                    "nautical", "cadet", "merchant navy", "bsm", "imec")
    if any(w in q for w in presea_words) and scores.get("presea", 0) > 0:
        return "presea"

    # "app" is more specific than admissions/contact when app-download keywords appear.
    app_words = ("himt app", "mobile app", "download the app", "download app", "play store",
                 "app store", "android app", "ios app", "iphone app", "google play", "app details")
    if any(w in q for w in app_words) and scores.get("app", 0) > 0:
        return "app"

    best = max(scores.items(), key=lambda x: x[1])
    return best[0] if best[1] > 0 else "general"


# ---------------------------------------------------------------------------
# Topic-owner map (Feb 2026)
# ---------------------------------------------------------------------------
# Three HIMT sites carry overlapping — and occasionally CONTRADICTORY — info.
# For high-stakes topics we hard-restrict retrieval to a single owner host so
# the assistant can never quote an outdated value from a secondary site.
#
#  - Fees, prices, batch dates            -> himt.co.in only (live booking)
#  - Pre-sea courses (DNS, GP, BSc)       -> himtcollege.com only
#
# Other topics keep their existing soft URL-boost behaviour.
# ---------------------------------------------------------------------------

_FEE_RX = re.compile(
    r"\b(fee|fees|price|prices|cost|costs|charge|charges|amount|"
    r"how\s+much|payable|payment|rupees?|inr|rs\.?)\b",
    re.I,
)
_BATCH_RX = re.compile(
    r"\b(batch|batches|schedule|slot|slots|start\s*dates?|next\s*batch|"
    r"upcoming\s*batch|when\s+does|when\s+is|when\s+will|available\s+from)\b",
    re.I,
)


def owner_hosts(query: str, intent: str) -> Optional[Set[str]]:
    """Return the SET of hosts that alone may answer this query, or None for no filter.

    A hard filter is applied only when the query semantics make one site the
    unambiguous source of truth. Everything else falls through to the existing
    soft URL-boost / intent-scoring pipeline.
    """
    q = query.lower()
    if _FEE_RX.search(q) or _BATCH_RX.search(q):
        return {"himt.co.in"}
    if intent == "presea":
        return {"himtcollege.com"}
    return None


# URL-path patterns that indicate a page is CANONICAL for a given intent.
INTENT_URL_PATTERNS = {
    "courses": ["himt.co.in", "/search#/courses", "/search#/packages", "/search#/home",
                "/basic-modular", "/advanced-modular", "/competency-courses", "/simulator-based",
                "/revalidation", "/value-added", "/uk-mca", "/vizag-course", "/virtual-reality-module",
                "/discounted-package", "/course"],
    # Pre-sea courses live on himtcollege.com, but LIVE fees/batch dates come from himt.co.in
    "presea": ["himt.co.in", "/search#/courses", "/search#/packages",
               "himtcollege.com", "/himt_courses/", "/bsm-eto", "/bsm-gme", "/imec-gme",
               "/join-merchant-navy", "/course/", "/placement/", "/e-brochure"],
    "about": ["/about-us", "/timeline-of-himt", "/our-faculty", "/campus", "/green-himt"],
    "admissions": ["/booking-procedures", "/submission-procedure", "/discounts-and-offers"],
    # Contact should surface HIMT's own contact pages ONLY — never nearby hotels.
    "contact": ["/contact", "/campus", "/about-us", "/our-faculty", "/faq"],
    # Documents info lives primarily in the FAQ and submission/booking procedure pages.
    "documents": ["/faq", "/submission-procedure", "/booking-procedures", "himt.co.in/search#/courses"],
}
# Pages we DE-boost when they win purely by lexical overlap.
DEPRIORITIZE_FOR_INTENT = {
    "courses": ["/industry-reviews", "/testimonial", "/press-clippings", "/discounts-and-offers"],
    "about": ["/industry-reviews", "/testimonial", "/press-clippings"],
    "admissions": ["/industry-reviews", "/testimonial"],
    "presea": ["/industry-reviews", "/testimonial", "/press-clippings"],
    "contact": ["/himt-nearby-accommodations", "/industry-reviews", "/testimonial", "/press-clippings"],
    "documents": ["/himt-nearby-accommodations", "/industry-reviews", "/testimonial"],
}

# Hard deny — URLs matching any of these substrings are dropped from retrieval entirely,
# regardless of intent. Used for pages that list third-party contact info.
HARD_DENY_URL_PATTERNS = ("/himt-nearby-accommodations",)


def _is_hard_denied(url: str) -> bool:
    u = (url or "").lower()
    return any(p in u for p in HARD_DENY_URL_PATTERNS)


def score_chunk(
    query_tokens: List[str], chunk_text: str, chunk_intent_tags: List[str],
    intent: str, page_url: str = "", query_lower: str = "",
) -> float:
    chunk_tokens = _tokenize(chunk_text)
    if not chunk_tokens:
        return 0.0

    chunk_counter = Counter(chunk_tokens)
    query_counter = Counter(query_tokens)

    # Term overlap score (weighted by term frequency within the chunk)
    overlap = 0.0
    for term, qcount in query_counter.items():
        if term in chunk_counter:
            overlap += qcount * (1 + min(chunk_counter[term], 3))

    # Normalise by chunk length (log)
    from math import log
    norm = 1.0 + log(1 + len(chunk_tokens))
    score = overlap / norm

    url_lower = (page_url or "").lower()

    # Canonical URL path for intent — grants base score AND boost, so course pages
    # can surface even when their text doesn't literally repeat query terms.
    is_canonical = False
    if intent in INTENT_URL_PATTERNS:
        for pat in INTENT_URL_PATTERNS[intent]:
            if pat in url_lower:
                is_canonical = True
                break

    if is_canonical:
        # Floor + large multiplier + additive bonus so canonical intent pages
        # decisively outrank generic HIMT-tokens pages.
        score = max(score, 0.5) * 4.0 + 4.0

    # Intent tag boost (softer, additive-multiplicative)
    if intent != "general" and intent in chunk_intent_tags:
        score = max(score, 0.3)
        score *= 1.3

    # De-prioritise reviews/testimonials/press for course/about/admissions intents
    if intent in DEPRIORITIZE_FOR_INTENT:
        for pat in DEPRIORITIZE_FOR_INTENT[intent]:
            if pat in url_lower:
                score *= 0.25
                break

    # Location-specific bias — if query mentions a campus city, decisively route to that page.
    if query_lower:
        for loc_key, boost_pats in LOCATION_URL_BOOST.items():
            if loc_key in query_lower:
                for pat in boost_pats:
                    if pat in url_lower:
                        score = max(score, 1.0) * 3.5 + 3.0
                        break
                for pat in LOCATION_URL_PENALTY.get(loc_key, []):
                    if pat in url_lower:
                        score *= 0.15
                        break

    # Penalise generic pages when intent has canonical pages elsewhere.
    is_homepage = url_lower.rstrip("/").endswith("himtmarine.com")
    generic_paths_for_courses = ("/about-us", "/timeline-of-himt", "/our-faculty", "/campus", "/green-himt", "/careers")
    if intent == "courses" and not is_canonical:
        if is_homepage or any(p in url_lower for p in generic_paths_for_courses):
            score *= 0.35

    if is_homepage and intent in ("admissions", "contact") and not is_canonical:
        score *= 0.4

    # Homepage boost for generic "about HIMT"
    if intent == "about" and is_homepage:
        score *= 1.4

    # Exact-phrase match bonus
    query_phrase = " ".join(query_tokens[:6])
    if query_phrase and query_phrase in chunk_text.lower():
        score *= 1.3

    return score


async def retrieve(query: str, db, top_k: int = 5) -> Tuple[List[Dict], str]:
    """Return (top_chunks, intent). Each chunk dict has text, page_url, page_title, score."""
    intent = detect_intent(query)
    query_tokens = _tokenize(query)
    if not query_tokens:
        return [], intent

    # Pull candidate chunks. Prefer intent-tagged first if query has clear intent.
    # Projection restricts to only fields the scorer needs.
    CHUNK_PROJ = {"_id": 0, "id": 1, "text": 1, "page_url": 1, "page_title": 1, "intent_tags": 1}
    candidates: List[Dict] = []
    seen_ids = set()
    if intent != "general":
        async for c in db.chunks.find({"intent_tags": intent}, CHUNK_PROJ).limit(200):
            candidates.append(c)
            seen_ids.add(c.get("id"))

        # Also pull chunks whose page_url matches a canonical URL pattern for this intent
        patterns = INTENT_URL_PATTERNS.get(intent, [])
        for pat in patterns:
            async for c in db.chunks.find(
                {"page_url": {"$regex": re.escape(pat), "$options": "i"}}, CHUNK_PROJ
            ).limit(100):
                if c.get("id") not in seen_ids:
                    candidates.append(c)
                    seen_ids.add(c.get("id"))

    # Add general candidates keyword-filtered — use Mongo regex to cut candidates server-side.
    # Query tokens are already lightly stemmed (fees->fee, batches->batch), so we allow the
    # regex to match the stem PLUS common suffixes so a "fee" token still matches "fees".
    query_terms = [re.escape(t) for t in query_tokens[:8]]
    if query_terms:
        mongo_regex = r"\b(" + "|".join(query_terms) + r")(?:s|es|ies|ed|ing)?\b"
        async for c in db.chunks.find(
            {"text": {"$regex": mongo_regex, "$options": "i"}}, CHUNK_PROJ
        ).limit(500):
            if c.get("id") in seen_ids:
                continue
            candidates.append(c)
            seen_ids.add(c.get("id"))

    # Fallback: if still nothing, pull first 100 chunks
    if not candidates:
        async for c in db.chunks.find({}, CHUNK_PROJ).limit(100):
            candidates.append(c)

    scored: List[Tuple[float, Dict]] = []
    query_lower = query.lower()
    for c in candidates:
        # Hard-deny: never surface chunks from denied URLs, regardless of intent.
        if _is_hard_denied(c.get("page_url", "")):
            continue
        s = score_chunk(
            query_tokens, c.get("text", ""), c.get("intent_tags", []),
            intent, c.get("page_url", ""), query_lower=query_lower,
        )
        if s > 0:
            scored.append((s, c))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Topic-owner hard filter: for high-stakes queries (fees, batches, pre-sea)
    # we drop chunks from non-owner hosts entirely so the assistant can never
    # quote an outdated value from a secondary site. If the filter would
    # eliminate ALL results, we fall back to the unfiltered ranking so the
    # user still gets *something* rather than a canned refusal.
    filter_hosts = owner_hosts(query, intent)
    if filter_hosts:
        from urllib.parse import urlparse

        def _host(u: str) -> str:
            return urlparse(u).netloc.replace("www.", "")

        filtered = [(s, c) for s, c in scored if _host(c.get("page_url", "")) in filter_hosts]
        if filtered:
            scored = filtered

    # Take top_k chunks BUT also keep the top-scored chunk from each additional
    # unique URL (up to source_urls_cap distinct URLs) — callers use these
    # extras to build a diverse `sources` list without paying the LLM-context
    # cost for the extra chunks.
    top: List[Dict] = []
    seen_urls: set = set()
    source_urls_cap = 12
    # First: fill top_k regardless of URL (highest scored chunks used by LLM).
    for s, c in scored[:top_k]:
        top.append({
            "text": c["text"],
            "page_url": c["page_url"],
            "page_title": c.get("page_title", ""),
            "score": round(s, 3),
        })
        seen_urls.add(c["page_url"])
    # Second: append additional unique-URL chunks (not used by LLM but surfaced
    # as sources) up to source_urls_cap distinct URLs total.
    for s, c in scored[top_k:]:
        if len(seen_urls) >= source_urls_cap:
            break
        if c["page_url"] in seen_urls:
            continue
        top.append({
            "text": c["text"],
            "page_url": c["page_url"],
            "page_title": c.get("page_title", ""),
            "score": round(s, 3),
        })
        seen_urls.add(c["page_url"])
    return top, intent


async def get_structured_facts(db) -> Dict:
    doc = await db.structured_facts.find_one({}, {"_id": 0})
    return doc or {}
