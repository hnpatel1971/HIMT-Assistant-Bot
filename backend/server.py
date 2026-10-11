"""HIMT AI Chatbot - FastAPI backend with retrieval, grounded AI answers, ingestion, and monitoring."""
import asyncio
import hashlib
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, APIRouter, HTTPException, Header, BackgroundTasks
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
import uuid

from models import (
    ChatRequest, ChatResponse, ChatLog, SourceRef,
    IngestionRun, StructuredFacts, Lead, LeadCreate,
    Feedback, FeedbackCreate,
)
from ingestion import crawl_and_ingest, crawl_himt_co_in
from retrieval import retrieve, get_structured_facts, detect_intent
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import resend
from anthropic import AsyncAnthropic

# ------------- Setup -------------
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
# No default: the repo is public, so admin endpoints stay locked until ADMIN_TOKEN is set.
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()
HIMT_URL = os.environ.get("HIMT_WEBSITE_URL", "https://himtmarine.com")
HIMT_ADDITIONAL_SITES = [s.strip() for s in os.environ.get("HIMT_ADDITIONAL_SITES", "").split(",") if s.strip()]
DAILY_REINGEST_HOUR = int(os.environ.get("DAILY_REINGEST_HOUR", "2"))  # 24h clock, UTC
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "").strip()
RESEND_FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL", "onboarding@resend.dev").strip()
WEAK_ANSWER_ALERT_TO = os.environ.get("WEAK_ANSWER_ALERT_TO", "").strip()
WEAK_ANSWER_ALERT_MIN_INTERVAL_SEC = int(os.environ.get("WEAK_ANSWER_ALERT_MIN_INTERVAL_SEC", "120"))
MODEL_PROVIDER = "anthropic"
MODEL_NAME = os.environ.get("LLM_MODEL", "").strip() or "claude-opus-5-5"
# Short grounded Q&A answers do well at low effort; raise via env if quality needs it.
LLM_EFFORT = os.environ.get("LLM_EFFORT", "").strip() or "low"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
logger = logging.getLogger("himt")

app = FastAPI(title="HIMT AI Chatbot")
api = APIRouter(prefix="/api")
scheduler = AsyncIOScheduler()

# In-process throttle for weak-answer emails so we never spam if many bad answers land in a row.
_last_alert_ts: dict = {"ts": 0.0}


def _send_resend_email(to_email: str, subject: str, html_body: str) -> None:
    """Synchronous Resend call — must be dispatched via asyncio.to_thread."""
    if not RESEND_API_KEY or not to_email:
        return
    resend.api_key = RESEND_API_KEY
    resend.Emails.send({
        "from": RESEND_FROM_EMAIL,
        "to": [to_email],
        "subject": subject,
        "html": html_body,
    })


async def _dispatch_weak_answer_alert(query: str, answer: str, status: str, intent: str,
                                     session_id: str, sources: list, log_id: str) -> None:
    """Send a weak-answer alert email. Never raises — logs failures silently."""
    if not (RESEND_API_KEY and WEAK_ANSWER_ALERT_TO):
        return
    # Throttle: skip if we sent one recently.
    now = time.time()
    if now - _last_alert_ts["ts"] < WEAK_ANSWER_ALERT_MIN_INTERVAL_SEC:
        logger.info("weak-answer alert throttled (min interval)")
        return
    _last_alert_ts["ts"] = now

    src_html = "".join(
        f"<li><a href='{s.get('url', '')}'>{(s.get('title') or s.get('url', ''))}</a></li>"
        for s in (sources or [])[:4]
    )
    status_color = {"insufficient": "#B45309", "fallback": "#B91C1C", "error": "#7F1D1D"}.get(status, "#374151")

    html = f"""
    <div style="font-family:Arial,sans-serif;color:#111827;max-width:640px;">
      <h2 style="margin:0 0 8px 0;color:#111827;">HIMT Chatbot — Weak-Answer Alert</h2>
      <p style="color:#6B7280;margin:0 0 20px 0;">A visitor received an answer flagged as <b style='color:{status_color}'>{status.upper()}</b>. Review below and consider adding this info to himtmarine.com.</p>

      <table cellpadding="8" cellspacing="0" border="0" style="border-collapse:collapse;width:100%;">
        <tr><td style="background:#F3F4F6;font-weight:bold;width:110px;">Query</td><td>{(query or '')[:500]}</td></tr>
        <tr><td style="background:#F3F4F6;font-weight:bold;">Intent</td><td>{intent}</td></tr>
        <tr><td style="background:#F3F4F6;font-weight:bold;">Status</td><td style="color:{status_color};font-weight:bold;">{status}</td></tr>
        <tr><td style="background:#F3F4F6;font-weight:bold;vertical-align:top;">Answer</td><td>{(answer or '')[:800]}</td></tr>
        <tr><td style="background:#F3F4F6;font-weight:bold;vertical-align:top;">Sources</td><td><ul style="margin:0;padding-left:16px;">{src_html or '<li>(none)</li>'}</ul></td></tr>
        <tr><td style="background:#F3F4F6;font-weight:bold;">Session</td><td style="font-family:monospace;font-size:12px;color:#6B7280;">{session_id}</td></tr>
        <tr><td style="background:#F3F4F6;font-weight:bold;">Log ID</td><td style="font-family:monospace;font-size:12px;color:#6B7280;">{log_id}</td></tr>
      </table>

      <p style="color:#6B7280;font-size:12px;margin-top:20px;">— HIMT AI Assistant · Automated alert · Throttled to 1 per {WEAK_ANSWER_ALERT_MIN_INTERVAL_SEC}s</p>
    </div>
    """

    subject = f"[HIMT Chatbot] Weak answer ({status}) — {(query or '')[:60]}"
    try:
        await asyncio.to_thread(_send_resend_email, WEAK_ANSWER_ALERT_TO, subject, html)
        logger.info(f"weak-answer alert sent to {WEAK_ANSWER_ALERT_TO} for query={query!r}")
    except Exception as e:
        logger.exception(f"weak-answer alert failed: {e}")


# ------------- Helpers -------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cache_key(query: str) -> str:
    normalized = " ".join(query.lower().split())
    return hashlib.sha256(normalized.encode()).hexdigest()[:32]


SYSTEM_PROMPT = """You are the official HIMT (Hindustan Institute of Maritime Training) assistant.
Your ONLY knowledge source is the CONTEXT provided below, extracted from the official himtmarine.com website.

RULES (strict):
1. Answer using ONLY facts from the CONTEXT. Do NOT invent or use outside knowledge.
2. Keep answers concise: 2-5 short sentences (max ~120 words). Use bullet points or a clean list for multi-item answers (courses, fees, documents).
3. If the CONTEXT does not clearly contain the answer, reply exactly: "I don't have that specific information from the HIMT website. Please contact HIMT directly for accurate details."
4. Do NOT fabricate phone numbers, emails, addresses, course names, or fees.
5. Do NOT say "based on the context" or "according to the provided content" — just answer naturally.
6. HIMT = Hindustan Institute of Maritime Training (an Indian maritime training institute).
7. If the user asks in a chatty way (e.g. greetings), respond briefly and offer to help with HIMT information.
8. LINKS: The CONTEXT contains SOURCE headers with the exact page URL. When the user asks for a "link", "URL", "website", or "where to book", ALWAYS include the most relevant source URL (from the SOURCE header) in your answer as a plain URL. Never say you don't have a link when a SOURCE URL is in the CONTEXT.
9. CAMPUS ADDRESS: If the user asks about campus / campus address / campus location and any SOURCE contains a street/city/pincode line, quote it verbatim in the answer (e.g. "11, Millers Road, Kilpauk, Chennai – 600 010"). Do not summarise addresses.
10. LOCATION-SPECIFIC FEES: If the user asks about a course at a SPECIFIC campus (Vizag / Chennai / Kalpakkam / etc.), you MUST use ONLY the fee that appears on the SOURCE page for THAT campus. Do NOT mix fees from other campus pages. If the user does not specify a campus, mention that fees may differ by campus.
11. COURSE LISTINGS: When listing multiple courses or fees, use a clean bullet list with the format `- Course Name — Duration — Fee` (omit fields that are missing). Do NOT write it as prose like "the X course is available from Y with fees Z"; keep it tabular.
12. DOCUMENTS: When the user asks about "required documents", "documents for admission", "documents for booking" — you MUST FIRST check whether the user has named a specific course.
    - IF the user has NOT named a course, DO NOT list documents. Instead, reply with a short clarifying question that asks them to pick a course and provide short options grouped as PRE-SEA (e.g. B.Sc Nautical Science / DNS / GME / ETO / GP Rating / B.Tech Marine Eng) and POST-SEA (e.g. PSCRB / Modular / Competency / UK-MCA). End with `Learn more: https://himtcollege.com/course/` and `Learn more: https://himtmarine.com/booking-procedures/`.
    - IF the user HAS named a course (e.g. "for DNS", "for PSCRB", "for GP Rating"), THEN list the documents from the CONTEXT. **Prefer the "Documents required for booking" block from the himt.co.in chunk for that course over any generic FAQ text**, because himt.co.in mirrors the live "Documents" link shown on the booking portal for that exact course. Only fall back to the general FAQ/booking-procedures document list if no himt.co.in docs block for that course appears in the CONTEXT.
    - Add a `Book online:` link (himt.co.in) so the user can proceed after collecting the papers — **only if the course is POST-SEA**. For PRE-SEA programmes (DNS, GP Rating, B.Sc / B.Tech, GME, ETO, OCCP, BSm, IMEC), do NOT emit any `Book online:` line — pre-sea admissions do NOT go through the online booking portal.
    Never dump a generic document list when the course is unknown — always disambiguate first.
13. COURSE HYPERLINKS: Whenever your answer is about a specific course, a group of courses, a program (pre-sea or post-sea), fees, duration, or a course-related enquiry, ALWAYS end the answer with a "Learn more:" line pointing to the most relevant SOURCE URL from the CONTEXT (e.g. `Learn more: https://himtcollege.com/course/`).
    - Prefer the MOST SPECIFIC page. If a dedicated per-course page exists in the CONTEXT (e.g. `/himt_courses/diploma-in-nautical-science/`, `/himt_courses/graduate-marine-engineering-gme/`, `/bsm-eto/`, `/vizag-course/`), use THAT URL — not the generic `/course/` or `/basic-modular-courses/` category page.
    - If the answer covers multiple distinct courses, add one `Learn more:` line per course with its dedicated URL.
    - Never omit this link for course answers.
14. FEES & BATCH DATES SOURCE OF TRUTH: For all questions involving course fees (₹ amounts), duration, batch start dates, or seat availability, the CONTEXT chunks from `himt.co.in` (the live HIMT Online Booking portal — URLs like `https://himt.co.in/Search#/Courses`, `.../Courses?val=REUC`, `.../Courses?val=VIZ`, `.../Packages`) are the AUTHORITATIVE source. If both a himt.co.in chunk and a himtmarine.com/himtcollege.com chunk contain fee or date info for the same course:
    - Quote the FEE and BATCH DATE from the himt.co.in chunk.
    - Add a `Book online: https://himt.co.in/` line so visitors can confirm and book directly on the live portal.
    - Still keep the `Learn more:` link to the dedicated course page (himtmarine.com/himtcollege.com) for descriptive content.
15. CLICKABLE LINKS: EVERY grounded answer MUST end with at least one clickable link so the user can verify or drill deeper.
    - Use markdown link syntax `[label](https://…)` for every URL you emit — do NOT paste bare URLs. Example: `Learn more: [PSCRB course](https://himtmarine.com/advanced-modular-courses/)`.
    - Use short human-friendly labels: `PSCRB course`, `Book online`, `Contact HIMT`, `HIMT Marine site`, `HIMT College site`, `HIMT app on Play Store`. Never use the raw URL as the label.
    - Always include a `Learn more:` link, and — for POST-SEA fee / batch / booking answers — also a `Book online:` link. Contact answers should include a `Contact HIMT:` link. NEVER emit `Book online:` on PRE-SEA answers.
    - Prefer the MOST SPECIFIC page URL from the CONTEXT over a category URL.
16. NEVER mention the number of available seats (SeatCount / "Seats: N") in any answer, even if it appears in the CONTEXT. Seat availability changes minute-by-minute and quoting a stale number damages trust — the user should click `Book online:` to see live availability.
"""


def _build_user_prompt(query: str, chunks, facts: dict, intent: str = "general") -> str:
    context_parts = []

    if facts:
        emails = facts.get("emails") or []
        phones = facts.get("phones") or []
        addresses = facts.get("addresses") or []
        if emails or phones or addresses:
            context_parts.append("=== STRUCTURED FACTS (verified) ===")
            if emails:
                context_parts.append("Emails: " + ", ".join(emails[:5]))
            if phones:
                context_parts.append("Phones: " + ", ".join(phones[:8]))
            if addresses:
                context_parts.append("Addresses:\n- " + "\n- ".join(addresses[:5]))
            context_parts.append("")

    # Intent-specific priming — helps the LLM handle ambiguous phrasings for known intents.
    if intent == "documents":
        context_parts.append("=== DOCUMENT-INTENT HINT ===")
        context_parts.append(
            "The user is asking about required documents. FIRST determine if the user's query "
            "names a specific course (e.g. 'for DNS', 'for PSCRB', 'for GP Rating', 'for GME'). "
            "If NO course is named, ask a short clarifying question offering PRE-SEA and POST-SEA "
            "options and DO NOT list documents.\n"
            "If a course IS named, list these baseline documents (they apply to all HIMT courses "
            "for INDoS number generation which is mandatory):\n"
            "- Copy of original 10th mark sheet\n"
            "- Copy of Passport\n"
            "- Passport size photo (white background)\n"
            "- Original documents to be brought in person to HIMT office\n"
            "Then add any course-specific requirements you see in the SOURCE, and a `Learn more:` "
            "URL to that course's page. End with: 'For the complete document list for your "
            "specific course, please contact HIMT directly.'"
        )
        context_parts.append("")

    if intent == "app":
        context_parts.append("=== APP-INTENT HINT (use these URLs directly) ===")
        context_parts.append(
            "The user is asking about the HIMT mobile app. Answer with a short 2-3 sentence intro "
            "and provide BOTH download links clearly labelled:\n"
            "- Android (Google Play): https://play.google.com/store/apps/details?id=com.imnv.im026.himt\n"
            "- iOS (App Store): https://apps.apple.com/in/app/himt/id1078900374\n"
            "Format them as clickable `Learn more:` / labelled URL lines. Also mention the "
            "official HIMT site: https://himtmarine.com/. Never say 'I don't have that information' "
            "for HIMT app questions — always provide these URLs."
        )
        context_parts.append("")

    if intent in ("presea", "courses", "admissions"):
        context_parts.append("=== COURSE URL MAP (use the MOST SPECIFIC URL that applies) ===")
        context_parts.append(
            "PRIMARY BOOKING PORTAL for all courses:\n"
            "- HIMT Online Booking (browse & book any course)  → https://himt.co.in/\n\n"
            "Pre-Sea (himtcollege.com):\n"
            "- B.Sc Nautical Science  → https://himtcollege.com/himt_courses/bsc-nautical-science-3-years/\n"
            "- B.Tech Marine Engineering  → https://himtcollege.com/himt_courses/btech-marine-engineering/\n"
            "- B.Tech Marine Engineering (Lateral Entry)  → https://himtcollege.com/himt_courses/btech-marine-engineering-lateral-entry/\n"
            "- Diploma in Nautical Science (DNS)  → https://himtcollege.com/himt_courses/diploma-in-nautical-science/\n"
            "- Electro-Technical Officer (ETO)  → https://himtcollege.com/himt_courses/electro-technical-officers-eto-4-months/\n"
            "- Graduate Marine Engineering (GME)  → https://himtcollege.com/himt_courses/graduate-marine-engineering-gme/\n"
            "- GP Rating / General Purpose Rating  → https://himtcollege.com/himt_courses/join-merchant-navy-course-general-purpose-rating/\n"
            "- OCCP (Orientation for Catering Personnel)  → https://himtcollege.com/himt_courses/orientation-course-for-catering-personnel/\n"
            "- BSm ETO (sponsored program)  → https://himtcollege.com/bsm-eto/\n"
            "- BSm GME (sponsored program)  → https://himtcollege.com/bsm-gme/\n"
            "- IMEC GME  → https://himtcollege.com/imec-gme/\n"
            "- Any generic pre-sea question  → https://himtcollege.com/course/\n\n"
            "Post-Sea (himtmarine.com):\n"
            "- Basic modular courses  → https://himtmarine.com/basic-modular-courses/\n"
            "- Advanced modular courses (incl. PSCRB)  → https://himtmarine.com/advanced-modular-courses/\n"
            "- Competency courses  → https://himtmarine.com/competency-courses/\n"
            "- Simulator-based courses  → https://himtmarine.com/simulator-based-courses/\n"
            "- Revalidation & Upgradation courses  → https://himtmarine.com/revalidation-upgradation-courses/\n"
            "- Value-added courses  → https://himtmarine.com/value-added-courses/\n"
            "- UK MCA approved courses  → https://himtmarine.com/uk-mca-approved-course/\n"
            "- UK MCA packages  → https://himtmarine.com/uk-mca-packages/\n"
            "- Vizag-specific courses  → https://himtmarine.com/vizag-course/\n"
            "- Booking / admissions procedure  → https://himtmarine.com/booking-procedures/\n\n"
            "RULES:\n"
            "1. When the answer names a specific course from this list, use its dedicated URL "
            "in a `Learn more:` line — never fall back to a generic category page if a dedicated "
            "URL exists here.\n"
            "2. If the answer is about a POST-SEA course / fees / batches, ALSO add a separate line "
            "pointing to the HIMT online booking portal: `Book online: https://himt.co.in/` — "
            "this is the authoritative source for live post-sea course details and booking.\n"
            "3. NEVER emit a `Book online:` line when the answer is about a PRE-SEA programme "
            "(B.Sc Nautical Science / B.Tech Marine Engineering / DNS / GME / ETO / GP Rating / "
            "OCCP / BSm / IMEC etc.). Pre-sea admissions happen through the college's admission "
            "process, NOT the online booking portal — showing `Book online:` there is wrong. "
            "For pre-sea answers, only emit `Learn more:` links pointing to himtcollege.com."
        )
        context_parts.append("")

    for i, c in enumerate(chunks, 1):
        context_parts.append(f"=== SOURCE {i}: {c['page_title']} ({c['page_url']}) ===")
        context_parts.append(c["text"])
        context_parts.append("")

    context = "\n".join(context_parts) if context_parts else "(no relevant content found)"

    return f"""CONTEXT:
{context}

USER QUESTION: {query}

Answer concisely using only the CONTEXT above."""


_claude_client: Optional[AsyncAnthropic] = None


def _get_claude_client() -> AsyncAnthropic:
    global _claude_client
    if _claude_client is None:
        _claude_client = AsyncAnthropic(api_key=ANTHROPIC_KEY)
    return _claude_client


async def _ask_llm(query: str, chunks, facts: dict, session_id: str, intent: str = "general") -> str:
    """Call Claude with the grounded prompt. Raise on failure so the caller can fall back."""
    if not ANTHROPIC_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    # max_tokens covers thinking + answer; answer length is kept short by SYSTEM_PROMPT.
    response = await _get_claude_client().beta.messages.create(
        model=MODEL_NAME,
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_user_prompt(query, chunks, facts, intent=intent)}],
        output_config={"effort": LLM_EFFORT},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined the request")
    answer = "".join(b.text for b in response.content if b.type == "text").strip()
    if not answer:
        raise RuntimeError(f"Claude returned no text (stop_reason={response.stop_reason})")
    return answer


# ------------- Routes -------------
@api.get("/")
async def root():
    return {"service": "HIMT AI Chatbot", "status": "ok", "model": f"{MODEL_PROVIDER}/{MODEL_NAME}"}


@api.get("/health")
async def health():
    pages = await db.pages.estimated_document_count()
    chunks = await db.chunks.estimated_document_count()
    facts_doc = await db.structured_facts.find_one({}, {"_id": 0})
    latest_run = await db.ingestion_runs.find_one(
        {}, {"_id": 0}, sort=[("started_at", -1)]
    )
    return {
        "status": "ok",
        "pages_indexed": pages,
        "chunks_indexed": chunks,
        "has_structured_facts": bool(facts_doc),
        "claude_configured": bool(ANTHROPIC_KEY),
        "latest_ingestion": latest_run,
    }


@api.post("/chat", response_model=ChatResponse)
async def chat_endpoint(req: ChatRequest):
    start = time.time()
    query = (req.message or "").strip()
    session_id = req.session_id or str(uuid.uuid4())

    if not query:
        raise HTTPException(status_code=400, detail="message is required")
    if len(query) > 1000:
        raise HTTPException(status_code=400, detail="message too long")

    ck = _cache_key(query)
    intent = detect_intent(query)

    # Check cache
    cached = await db.chat_cache.find_one({"key": ck}, {"_id": 0})
    if cached:
        latency = int((time.time() - start) * 1000)
        offer_lead = intent == "admissions" and cached["status"] in ("grounded", "insufficient")
        log = ChatLog(
            session_id=session_id, query=query, answer=cached["answer"],
            sources=cached.get("sources", []), status=cached["status"],
            intent=intent, latency_ms=latency, cached=True,
        )
        await db.chat_logs.insert_one(log.model_dump())
        return ChatResponse(
            session_id=session_id,
            answer=cached["answer"],
            sources=[SourceRef(**s) for s in cached.get("sources", [])],
            status=cached["status"],
            cached=True,
            latency_ms=latency,
            intent=intent,
            offer_lead_capture=offer_lead,
            log_id=log.id,
        )

    # Retrieve
    top_chunks, detected_intent = await retrieve(query, db, top_k=5)
    facts = await get_structured_facts(db)

    # Build sources — pick the top-scored chunk from each unique URL, then
    # prefer host diversity, so course queries surface himt.co.in (fees/batches)
    # AND himtmarine.com/himtcollege.com (descriptions) side-by-side.
    from urllib.parse import urlparse
    per_url_best = []  # ordered list of (url, title) — first occurrence wins (highest score)
    seen_urls = set()
    for c in top_chunks:
        if c["page_url"] in seen_urls:
            continue
        seen_urls.add(c["page_url"])
        per_url_best.append({"url": c["page_url"], "title": c["page_title"] or c["page_url"]})

    sources = []
    seen_hosts = set()
    picked = set()
    for i, src in enumerate(per_url_best):
        host = urlparse(src["url"]).netloc.replace("www.", "")
        if host in seen_hosts:
            continue
        seen_hosts.add(host)
        picked.add(i)
        sources.append(src)
        if len(sources) >= 4:
            break
    for i, src in enumerate(per_url_best):
        if len(sources) >= 4:
            break
        if i in picked:
            continue
        sources.append(src)

    # LLM context: at most 5 chunks total, and at most 2 chunks per canonical
    # URL — with ONE exception: the himt.co.in booking catalogue URL is a
    # single page_url that carries one chunk PER course, so we allow up to
    # 4 chunks from it so a course-specific query (e.g. "AFF documents") can
    # actually see the AFF course chunk alongside neighbours. URLs are
    # canonicalised (strip 'www.' + drop trailing slash) so a page crawled
    # under both example.com/x/ and www.example.com/x/ counts as ONE.
    def _canon_url(u: str) -> str:
        from urllib.parse import urlparse, urlunparse
        p = urlparse(u)
        host = p.netloc.replace("www.", "")
        path = p.path.rstrip("/") or "/"
        return urlunparse((p.scheme, host, path, "", "", p.fragment))

    HIMT_CO_IN_MULTI_CHUNK_URLS = {
        "https://himt.co.in/Search#/Courses",
        "https://himt.co.in/Search#/Packages",
    }

    def _cap_for(url: str) -> int:
        return 4 if url in HIMT_CO_IN_MULTI_CHUNK_URLS else 2

    context_chunks: list = []
    per_url_count: dict = {}
    for c in top_chunks:
        key = _canon_url(c["page_url"])
        cnt = per_url_count.get(key, 0)
        if cnt >= _cap_for(c["page_url"]):
            continue
        per_url_count[key] = cnt + 1
        context_chunks.append(c)
        if len(context_chunks) >= 5:
            break
    top_chunks = context_chunks

    # Call LLM
    status = "grounded"
    try:
        if not top_chunks and not (facts.get("emails") or facts.get("phones")):
            answer = "I don't have that specific information from the HIMT website. Please contact HIMT directly for accurate details."
            status = "insufficient"
        else:
            answer = await _ask_llm(query, top_chunks, facts, session_id, intent=detected_intent)
            # If model says it doesn't know, mark insufficient
            if "don't have that specific information" in answer.lower() or "do not have that specific information" in answer.lower():
                status = "insufficient"
    except Exception as e:
        logger.exception("LLM call failed")
        # Graceful fallback
        answer = _rule_based_fallback(query, top_chunks, facts)
        status = "fallback"

    latency = int((time.time() - start) * 1000)

    # Cache successful grounded answers
    if status == "grounded":
        await db.chat_cache.update_one(
            {"key": ck},
            {"$set": {
                "key": ck,
                "query": query,
                "answer": answer,
                "sources": sources,
                "status": status,
                "created_at": _now_iso(),
            }},
            upsert=True,
        )

    log = ChatLog(
        session_id=session_id, query=query, answer=answer,
        sources=sources, status=status, intent=detected_intent,
        latency_ms=latency, cached=False,
    )
    await db.chat_logs.insert_one(log.model_dump())

    # Fire weak-answer alert in the background if this reply is not grounded.
    if status in ("insufficient", "fallback", "error"):
        asyncio.create_task(_dispatch_weak_answer_alert(
            query=query, answer=answer, status=status, intent=detected_intent,
            session_id=session_id, sources=sources, log_id=log.id,
        ))

    return ChatResponse(
        session_id=session_id,
        answer=answer,
        sources=[SourceRef(**s) for s in sources],
        status=status,
        cached=False,
        latency_ms=latency,
        intent=detected_intent,
        offer_lead_capture=(detected_intent == "admissions" and status in ("grounded", "insufficient")),
        log_id=log.id,
    )


def _rule_based_fallback(query: str, chunks, facts: dict) -> str:
    """When LLM fails: build a rule-based answer from retrieved chunks + facts."""
    q = query.lower()

    # Contact intent — return structured facts directly
    if any(w in q for w in ["contact", "phone", "email", "address", "reach", "call", "mail"]):
        lines = ["HIMT contact details from our records:"]
        if facts.get("emails"):
            lines.append("Email: " + ", ".join(facts["emails"][:3]))
        if facts.get("phones"):
            lines.append("Phone: " + ", ".join(facts["phones"][:4]))
        if facts.get("addresses"):
            lines.append("Address: " + facts["addresses"][0])
        if len(lines) == 1:
            return "I'm having trouble reaching the AI service right now. Please try again shortly or visit himtmarine.com for contact information."
        lines.append("(AI service is temporarily unavailable — showing verified stored details.)")
        return "\n".join(lines)

    if chunks:
        excerpt = chunks[0]["text"][:400].strip()
        return (
            "AI service is temporarily unavailable. Here's the most relevant excerpt from the HIMT website:\n\n"
            f"{excerpt}...\n\nSource: {chunks[0]['page_url']}"
        )

    return "I'm having trouble reaching the AI service right now. Please try again shortly, or visit himtmarine.com."


# ------------- Admin -------------
def _check_admin(token: Optional[str]):
    if not token or token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="invalid admin token")


@api.post("/admin/reingest")
async def admin_reingest(background: BackgroundTasks, x_admin_token: Optional[str] = Header(None)):
    _check_admin(x_admin_token)
    # In-flight guard: if a previous run started within the last 10 min and hasn't
    # finished, don't kick off a parallel run — otherwise both clear chunks then
    # both insert, producing duplicates.
    now = datetime.now(timezone.utc)
    async for r in db.ingestion_runs.find(
        {"status": {"$ne": "completed"}}, sort=[("started_at", -1)]
    ).limit(1):
        started = r.get("started_at")
        if started:
            try:
                started_dt = datetime.fromisoformat(started)
                if (now - started_dt).total_seconds() < 600:
                    return {"run_id": r["id"], "status": "already_running"}
            except Exception:
                pass
    # Also refuse if the last COMPLETED run finished in the last 20 seconds
    # (protects against the browser's double-click / retry storm).
    last = await db.ingestion_runs.find_one(
        {"status": "completed"}, sort=[("finished_at", -1)],
    )
    if last and last.get("finished_at"):
        try:
            finished_dt = datetime.fromisoformat(last["finished_at"])
            if (now - finished_dt).total_seconds() < 20:
                return {"run_id": last["id"], "status": "throttled"}
        except Exception:
            pass
    run_id = await _start_ingestion_run()
    background.add_task(_run_ingestion, run_id)
    return {"run_id": run_id, "status": "started"}


async def _start_ingestion_run() -> str:
    run = IngestionRun()
    await db.ingestion_runs.insert_one(run.model_dump())
    return run.id


async def _run_ingestion(run_id: str, trigger: str = "manual"):
    """Reusable ingestion runner used by admin API and scheduler."""
    try:
        result = await crawl_and_ingest(HIMT_URL, db, run_id, additional_sites=HIMT_ADDITIONAL_SITES)
        await db.ingestion_runs.update_one(
            {"id": run_id},
            {"$set": {
                "finished_at": _now_iso(),
                "status": "completed",
                "pages_crawled": result["pages_crawled"],
                "chunks_created": result["chunks_created"],
                "errors": result["errors"][:20],
                "trigger": trigger,
            }},
        )
        await db.chat_cache.delete_many({})
        logger.info(f"Ingestion {run_id} ({trigger}) completed: {result['pages_crawled']} pages, {result['chunks_created']} chunks")
    except Exception as e:
        logger.exception("ingestion failed")
        await db.ingestion_runs.update_one(
            {"id": run_id},
            {"$set": {"finished_at": _now_iso(), "status": "failed", "errors": [str(e)], "trigger": trigger}},
        )


async def _scheduled_ingestion():
    """Fired by APScheduler daily. Skips if a run finished within the last 6 hours."""
    recent = await db.ingestion_runs.find_one(
        {"status": "completed", "finished_at": {"$exists": True}},
        sort=[("finished_at", -1)],
    )
    if recent and recent.get("finished_at"):
        try:
            finished_dt = datetime.fromisoformat(recent["finished_at"])
            age_hours = (datetime.now(timezone.utc) - finished_dt).total_seconds() / 3600
            if age_hours < 6:
                logger.info(f"Scheduled ingestion skipped — last run only {age_hours:.1f}h ago.")
                return
        except Exception:
            pass
    logger.info("Scheduled daily ingestion starting…")
    run_id = await _start_ingestion_run()
    await _run_ingestion(run_id, trigger="scheduled")


async def _scheduled_himt_co_in_refresh():
    """Refresh only himt.co.in chunks (fees + batch dates change often).

    Runs every 6 hours. Skips if a full ingestion or himt.co.in refresh
    completed within the last 90 minutes to avoid overlap.
    """
    recent = await db.ingestion_runs.find_one(
        {"status": "completed", "finished_at": {"$exists": True},
         "trigger": {"$in": ["scheduled", "himt_co_in_refresh", "manual", "boot"]}},
        sort=[("finished_at", -1)],
    )
    if recent and recent.get("finished_at"):
        try:
            finished_dt = datetime.fromisoformat(recent["finished_at"])
            age_min = (datetime.now(timezone.utc) - finished_dt).total_seconds() / 60
            if age_min < 90:
                logger.info(f"himt.co.in refresh skipped — last run only {age_min:.1f}m ago.")
                return
        except Exception:
            pass

    logger.info("himt.co.in refresh starting…")
    run = IngestionRun()
    await db.ingestion_runs.insert_one({**run.model_dump(), "trigger": "himt_co_in_refresh"})
    try:
        # Replace only himt.co.in chunks + pages (leave himtmarine.com / himtcollege.com untouched).
        await db.chunks.delete_many({"page_url": {"$regex": "himt\\.co\\.in"}})
        await db.pages.delete_many({"url": {"$regex": "himt\\.co\\.in"}})
        result = await crawl_himt_co_in("https://himt.co.in", db)
        await db.ingestion_runs.update_one(
            {"id": run.id},
            {"$set": {
                "finished_at": _now_iso(),
                "status": "completed",
                "pages_crawled": result["pages_crawled"],
                "chunks_created": result["chunks_created"],
                "errors": result["errors"][:20],
                "trigger": "himt_co_in_refresh",
            }},
        )
        await db.chat_cache.delete_many({})
        logger.info(
            f"himt.co.in refresh completed: {result['pages_crawled']} pages, "
            f"{result['chunks_created']} chunks"
        )
    except Exception as e:
        logger.exception("himt.co.in refresh failed")
        await db.ingestion_runs.update_one(
            {"id": run.id},
            {"$set": {"finished_at": _now_iso(), "status": "failed",
                      "errors": [str(e)], "trigger": "himt_co_in_refresh"}},
        )


@api.get("/admin/runs")
async def admin_runs(x_admin_token: Optional[str] = Header(None), limit: int = 20):
    _check_admin(x_admin_token)
    runs = await db.ingestion_runs.find({}, {"_id": 0}).sort("started_at", -1).to_list(limit)
    return {"runs": runs}


@api.get("/admin/logs")
async def admin_logs(
    x_admin_token: Optional[str] = Header(None),
    limit: int = 100,
    status: Optional[str] = None,
):
    _check_admin(x_admin_token)
    q = {}
    if status:
        q["status"] = status
    logs = await db.chat_logs.find(q, {"_id": 0}).sort("created_at", -1).to_list(limit)
    return {"logs": logs}


@api.get("/admin/stats")
async def admin_stats(x_admin_token: Optional[str] = Header(None)):
    _check_admin(x_admin_token)
    total = await db.chat_logs.count_documents({})
    grounded = await db.chat_logs.count_documents({"status": "grounded"})
    insufficient = await db.chat_logs.count_documents({"status": "insufficient"})
    fallback = await db.chat_logs.count_documents({"status": "fallback"})
    errors = await db.chat_logs.count_documents({"status": "error"})
    cached_count = await db.chat_logs.count_documents({"cached": True})
    pages = await db.pages.count_documents({})
    chunks = await db.chunks.count_documents({})
    return {
        "total_queries": total,
        "grounded": grounded,
        "insufficient": insufficient,
        "fallback": fallback,
        "errors": errors,
        "cache_hits": cached_count,
        "pages_indexed": pages,
        "chunks_indexed": chunks,
    }


@api.get("/admin/verify")
async def admin_verify(x_admin_token: Optional[str] = Header(None)):
    """Simple verification of admin token — used by admin UI on login."""
    _check_admin(x_admin_token)
    return {"ok": True}


@api.get("/admin/facts")
async def admin_facts(x_admin_token: Optional[str] = Header(None)):
    _check_admin(x_admin_token)
    doc = await db.structured_facts.find_one({}, {"_id": 0})
    return {"facts": doc or {}}


@api.get("/admin/schedule")
async def admin_schedule(x_admin_token: Optional[str] = Header(None)):
    _check_admin(x_admin_token)
    jobs = []
    for j in scheduler.get_jobs():
        jobs.append({
            "id": j.id,
            "name": j.name,
            "next_run_time": j.next_run_time.isoformat() if j.next_run_time else None,
            "trigger": str(j.trigger),
        })
    return {"enabled": scheduler.running, "daily_hour_utc": DAILY_REINGEST_HOUR, "jobs": jobs}


@api.post("/admin/test-alert")
async def admin_test_alert(x_admin_token: Optional[str] = Header(None)):
    """Send a sample weak-answer alert email to WEAK_ANSWER_ALERT_TO — for verification only."""
    _check_admin(x_admin_token)
    if not RESEND_API_KEY:
        raise HTTPException(status_code=400, detail="RESEND_API_KEY not configured")
    if not WEAK_ANSWER_ALERT_TO:
        raise HTTPException(status_code=400, detail="WEAK_ANSWER_ALERT_TO not configured")
    # Bypass throttle for the manual test.
    _last_alert_ts["ts"] = 0.0
    await _dispatch_weak_answer_alert(
        query="[TEST] Manual admin trigger",
        answer="This is a test alert to confirm the Resend integration is live. No visitor sent this.",
        status="insufficient",
        intent="test",
        session_id="admin-test",
        sources=[{"url": "https://himtmarine.com", "title": "himtmarine.com"}],
        log_id="admin-test-log",
    )
    return {"ok": True, "sent_to": WEAK_ANSWER_ALERT_TO, "from": RESEND_FROM_EMAIL}


# ------------- Feedback (public POST, admin GET) -------------
@api.post("/feedback")
async def submit_feedback(payload: FeedbackCreate):
    if payload.sentiment not in ("up", "down"):
        raise HTTPException(status_code=400, detail="sentiment must be 'up' or 'down'")

    # Prevent duplicates from repeated clicks on the same message
    if payload.log_id:
        existing = await db.feedback.find_one({"log_id": payload.log_id})
        if existing:
            await db.feedback.update_one(
                {"log_id": payload.log_id},
                {"$set": {"sentiment": payload.sentiment, "created_at": _now_iso()}},
            )
            return {"ok": True, "updated": True}

    fb = Feedback(
        log_id=payload.log_id,
        session_id=payload.session_id,
        query=(payload.query or "")[:500],
        answer_preview=(payload.answer_preview or "")[:500],
        sentiment=payload.sentiment,
        intent=payload.intent,
        status=payload.status,
    )
    await db.feedback.insert_one(fb.model_dump())
    logger.info(f"Feedback {payload.sentiment} on log_id={payload.log_id} query={(payload.query or '')[:80]!r}")
    return {"ok": True, "feedback_id": fb.id}


@api.get("/admin/feedback")
async def admin_feedback(
    x_admin_token: Optional[str] = Header(None),
    sentiment: Optional[str] = None,
    limit: int = 200,
):
    _check_admin(x_admin_token)
    q = {}
    if sentiment in ("up", "down"):
        q["sentiment"] = sentiment
    items = await db.feedback.find(q, {"_id": 0}).sort("created_at", -1).to_list(limit)
    up = await db.feedback.count_documents({"sentiment": "up"})
    down = await db.feedback.count_documents({"sentiment": "down"})
    return {"feedback": items, "counts": {"up": up, "down": down, "total": up + down}}


# ------------- Leads (public POST, admin GET) -------------
@api.post("/leads")
async def create_lead(payload: LeadCreate):
    # Basic validation
    if not payload.name or not payload.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    if not payload.phone or len(re.sub(r"\D", "", payload.phone)) < 8:
        raise HTTPException(status_code=400, detail="valid phone is required")

    lead = Lead(
        name=payload.name.strip()[:120],
        phone=payload.phone.strip()[:40],
        email=(payload.email or "").strip()[:200] or None,
        interest=(payload.interest or "").strip()[:200] or None,
        session_id=payload.session_id,
        source_query=(payload.source_query or "")[:500] or None,
    )
    await db.leads.insert_one(lead.model_dump())
    logger.info(f"Lead captured: {lead.name} / {lead.phone} (session={lead.session_id})")
    return {"ok": True, "lead_id": lead.id}


@api.get("/admin/leads")
async def admin_leads(x_admin_token: Optional[str] = Header(None), limit: int = 200, status: Optional[str] = None):
    _check_admin(x_admin_token)
    q = {}
    if status:
        q["status"] = status
    leads = await db.leads.find(q, {"_id": 0}).sort("created_at", -1).to_list(limit)
    return {"leads": leads, "total": await db.leads.count_documents({})}


@api.patch("/admin/leads/{lead_id}")
async def admin_update_lead(lead_id: str, body: dict, x_admin_token: Optional[str] = Header(None)):
    _check_admin(x_admin_token)
    allowed = {k: v for k, v in body.items() if k in {"status"}}
    if not allowed:
        raise HTTPException(status_code=400, detail="no updatable fields provided")
    if "status" in allowed and allowed["status"] not in {"new", "contacted", "closed"}:
        raise HTTPException(status_code=400, detail="invalid status")
    result = await db.leads.update_one({"id": lead_id}, {"$set": allowed})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="lead not found")
    return {"ok": True}


# ------------- Suggested prompts (public) -------------
@api.get("/suggested-prompts")
async def suggested_prompts():
    return {
        "prompts": [
            {"label": "Tell me about HIMT", "text": "Tell me about HIMT"},
            {"label": "Courses", "text": "What courses does HIMT offer?"},
            {"label": "Admissions", "text": "How do I apply for a course at HIMT?"},
            {"label": "Contact", "text": "What is HIMT's contact number and email?"},
        ]
    }



# ------------- App wiring -------------
app.include_router(api)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

# When WEB_DIST_DIR points at the built frontend (frontend/build), serve it from
# this same process so one service and one port host both the API and the widget.
WEB_DIST_DIR = os.environ.get("WEB_DIST_DIR", "").strip()
if WEB_DIST_DIR:
    from fastapi.responses import FileResponse

    _web_root = Path(WEB_DIST_DIR).resolve()

    @app.get("/{path:path}", include_in_schema=False)
    async def web_app(path: str):
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not found")
        candidate = (_web_root / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(_web_root):
            return FileResponse(candidate)
        return FileResponse(_web_root / "index.html")


@app.on_event("startup")
async def startup_event():
    # Ensure indexes
    await db.chunks.create_index("intent_tags")
    await db.chunks.create_index("page_url")
    await db.chat_logs.create_index("created_at")
    await db.chat_cache.create_index("key", unique=True)
    await db.ingestion_runs.create_index("started_at")
    await db.leads.create_index("created_at")
    await db.feedback.create_index("created_at")
    await db.feedback.create_index("log_id")
    logger.info("HIMT chatbot backend ready.")

    # Schedule daily re-ingestion at DAILY_REINGEST_HOUR UTC (default 02:00 UTC)
    try:
        scheduler.add_job(
            _scheduled_ingestion,
            CronTrigger(hour=DAILY_REINGEST_HOUR, minute=0),
            id="daily-ingest",
            name="Daily HIMT re-ingestion",
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=3600,
        )
        # himt.co.in changes batch dates/fees frequently — refresh every 6 hours.
        scheduler.add_job(
            _scheduled_himt_co_in_refresh,
            CronTrigger(hour="*/6", minute=30),
            id="himt-co-in-refresh",
            name="himt.co.in 6-hourly refresh (courses/packages)",
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=1800,
        )
        scheduler.start()
        logger.info(
            f"Scheduler started — daily ingestion at {DAILY_REINGEST_HOUR:02d}:00 UTC + "
            f"himt.co.in refresh every 6h"
        )
    except Exception:
        logger.exception("Failed to start scheduler")

    # Boot self-heal — a single redeploy should be enough to converge production
    # to the correct index shape, no manual /api/admin/reingest curl needed.
    #
    # We refire a full ingestion when ANY of the following are true:
    #   1. The DB is empty (first-ever boot).
    #   2. himt.co.in chunks are missing (index predates the JSON crawler).
    #   3. The index looks bloated — most likely because two /api/admin/reingest
    #      calls overlapped in a previous release before the in-flight guard
    #      existed, doubling every chunk. A healthy full crawl produces ~85
    #      pages / ~1,250 chunks; anything materially above that means dupes.
    try:
        chunks = await db.chunks.count_documents({})
        himt_co_in_chunks = await db.chunks.count_documents(
            {"page_url": {"$regex": r"himt\.co\.in"}}
        )
        pages = await db.pages.count_documents({})
        BLOAT_CHUNK_THRESHOLD = 1500
        BLOAT_PAGE_THRESHOLD = 120
        needs_heal = (
            chunks == 0
            or himt_co_in_chunks == 0
            or chunks > BLOAT_CHUNK_THRESHOLD
            or pages > BLOAT_PAGE_THRESHOLD
        )
        if needs_heal:
            logger.info(
                f"Boot self-heal firing — chunks={chunks} "
                f"himt_co_in_chunks={himt_co_in_chunks} pages={pages}."
            )
            run_id = await _start_ingestion_run()
            asyncio.create_task(_run_ingestion(run_id, trigger="boot"))
        else:
            logger.info(
                f"Boot self-heal skipped — index looks healthy "
                f"(chunks={chunks}, himt_co_in_chunks={himt_co_in_chunks}, pages={pages})."
            )
    except Exception:
        logger.exception("Boot self-heal check failed")


@app.on_event("shutdown")
async def shutdown_event():
    try:
        if scheduler.running:
            scheduler.shutdown(wait=False)
    except Exception:
        pass
    client.close()
