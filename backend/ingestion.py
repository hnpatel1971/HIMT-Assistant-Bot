"""HIMT website ingestion: crawl → clean → chunk → structured extraction."""
import asyncio
import re
from typing import List, Set, Tuple, Dict
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from models import CrawledPage, Chunk, StructuredFacts

# Regexes for structured extraction
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
# Indian phone patterns, plus generic international
PHONE_RE = re.compile(
    r"(?:\+?\d{1,3}[\s-]?)?(?:\(?\d{2,4}\)?[\s-]?)?\d{3,4}[\s-]?\d{3,4}(?:[\s-]?\d{2,4})?"
)
# Priority pages for HIMT
PRIORITY_PATHS = [
    "/",
    "/about-us/",
    "/our-faculty/",
    "/campus/",
    "/timeline-of-himt/",
    "/careers/",
    "/faq/",
    "/booking-procedures/",
    "/submission-procedure/",
    "/discounts-and-offers/",
    "/discounted-package/",
    "/basic-modular-courses/",
    "/advanced-modular-courses/",
    "/competency-courses/",
    "/simulator-based-courses/",
    "/revalidation-upgradation-courses/",
    "/value-added-courses/",
    "/uk-mca-approved-course/",
    "/uk-mca-packages/",
    "/vizag-course/",
    "/virtual-reality-module/",
    "/himt-nearby-accommodations/",
    "/industry-reviews/",
    "/testimonial/",
    "/press-clippings/",
]

INTENT_KEYWORDS = {
    "contact": ["contact", "phone", "email", "address", "reach", "call", "mail", "location", "office"],
    "admissions": ["admission", "apply", "enrol", "enroll", "booking", "submission", "eligibility", "fee", "fees", "procedure"],
    "courses": ["course", "training", "program", "syllabus", "modular", "competency", "simulator", "mca", "dg shipping", "revalidation"],
    "about": ["about", "history", "faculty", "campus", "himt", "hindustan", "maritime", "institute", "founded", "timeline", "accreditation"],
    "career": ["career", "job", "faculty", "recruit"],
}

# Boilerplate lines to drop (nav/footer noise)
NOISE_PATTERNS = [
    "Toggle navigation",
    "Online Booking",
    "Generic selectors",
    "Exact matches only",
    "Search in title",
    "Search in content",
    "Post Type Selectors",
    "Student Login",
    "Certificate Verification",
    "himt-banner",
    "Copyright",
    "All rights reserved",
    "Powered by",
]


def _clean_text(text: str) -> str:
    """Remove noise lines, collapse whitespace."""
    lines = [ln.strip() for ln in text.split("\n")]
    cleaned = []
    for ln in lines:
        if not ln or len(ln) < 3:
            continue
        if any(noise.lower() in ln.lower() for noise in NOISE_PATTERNS):
            continue
        cleaned.append(ln)
    text = "\n".join(cleaned)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_main_text(html: str) -> Tuple[str, str]:
    """Return (title, body_text) with nav/footer/scripts stripped."""
    soup = BeautifulSoup(html, "lxml")

    # Remove non-content elements
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "iframe"]):
        tag.decompose()
    # Remove common boilerplate containers
    for sel in [".menu", ".navbar", ".header", ".footer", ".sidebar", "#header", "#footer", "#menu"]:
        for el in soup.select(sel):
            el.decompose()

    title = soup.title.string.strip() if soup.title and soup.title.string else ""

    # Prefer <main>, <article>, or #content
    main = soup.find("main") or soup.find("article") or soup.find(id="content") or soup.body
    text = main.get_text("\n", strip=True) if main else soup.get_text("\n", strip=True)
    return title, _clean_text(text)


def _detect_intents(text: str) -> List[str]:
    text_lower = text.lower()
    tags = []
    for intent, kws in INTENT_KEYWORDS.items():
        for kw in kws:
            if kw in text_lower:
                tags.append(intent)
                break
    return tags


def _chunk_text(text: str, target_words: int = 180, overlap_words: int = 30) -> List[str]:
    """Sliding-window paragraph-aware chunking."""
    # Split by paragraph first
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    chunks: List[str] = []
    buf: List[str] = []
    buf_words = 0

    def flush():
        nonlocal buf, buf_words
        if buf:
            chunks.append(" ".join(buf).strip())
        buf = []
        buf_words = 0

    for p in paragraphs:
        w = p.split()
        if len(w) > target_words:
            # Split long paragraph into sentences
            sentences = re.split(r"(?<=[.!?])\s+", p)
            for s in sentences:
                sw = s.split()
                if buf_words + len(sw) > target_words:
                    flush()
                buf.append(s)
                buf_words += len(sw)
        else:
            if buf_words + len(w) > target_words:
                flush()
            buf.append(p)
            buf_words += len(w)
    flush()

    # Add overlap between chunks
    if len(chunks) > 1 and overlap_words > 0:
        overlapped = [chunks[0]]
        for i in range(1, len(chunks)):
            prev_words = chunks[i - 1].split()[-overlap_words:]
            overlapped.append(" ".join(prev_words) + " " + chunks[i])
        chunks = overlapped

    return [c for c in chunks if len(c.split()) >= 15]


HIMT_EMAIL_DOMAINS = ("himtmarine.com", "himtcollege.com", "himtoffshore.com", "himt.co.in", "himt.in")
# Domains we KNOW are third-party embeds / boilerplate - always drop.
BLOCKED_EMAIL_DOMAINS = ("pridehotel", "myftpupload", "wixmp", "example.com", "sentry", "google", "wordpress")


def _valid_himt_email(e: str) -> bool:
    el = e.lower().strip()
    if any(bd in el for bd in BLOCKED_EMAIL_DOMAINS):
        return False
    return any(el.endswith("@" + d) or ("@" in el and d in el.split("@", 1)[1]) for d in HIMT_EMAIL_DOMAINS)


# Date-like patterns to reject when detecting phones
DATE_PATTERN = re.compile(r"^\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}$")


def _valid_phone(match: str) -> bool:
    m = match.strip()
    digits = re.sub(r"\D", "", m)
    if not (10 <= len(digits) <= 13):
        return False
    # Reject dates like 01-25-2020, 2019-2020, etc.
    if DATE_PATTERN.match(m.replace(" ", "")):
        return False
    # Reject if it's just years (e.g. 20192020)
    if len(digits) == 8 and digits.startswith(("19", "20")):
        return False
    # Reject Chennai postal-code prefixes joined with a partial number (e.g. "600113 9941")
    # Chennai pincodes are 600001–600130. If the digits START with 600 followed by three digits
    # that fall in that range and total digits < 12, it's almost certainly a pincode fragment.
    if digits.startswith("600") and len(digits) <= 11:
        try:
            if 600001 <= int(digits[:6]) <= 600130:
                return False
        except ValueError:
            pass
    # Must contain either +91 or start with a valid indian prefix or landline pattern
    if m.startswith("+"):
        return True
    if digits.startswith(("91", "0")) or len(digits) == 10:
        return True
    # Landline patterns like 044-XXXXXXX
    if re.match(r"^0\d{2,4}[-\s]?\d{5,8}$", m):
        return True
    return False


# Pages that carry HIMT's OWN contact facts (emails / phones / addresses).
# Fact extraction is restricted to these — third-party listings (hotels near campus,
# testimonials, reviews) MUST NOT contribute contact facts.
HIMT_OWN_PAGE_ALLOWLIST = (
    "/campus/",
    "/about-us/",
    "/about-himt-college/",
    "/contact",
    "/our-faculty/",
    "/timeline-of-himt/",
    "/green-himt/",
    "/faq/",
    "/careers/",
    "/booking-procedures/",
    "/submission-procedure/",
    # himtcollege.com — pre-sea admissions & contact
    "/join-merchant-navy/",
    "/placement/",
    "/e-brochure/",
    "/ceos-desk/",
    "/course/",
    "/himt_courses/",
    # himt.co.in SPA pages that carry HIMT-own contact info
    "/contactus.html",
    "/faq.aspx",
    "/search#/home",
    "/search#/courses",
    "/search#/packages",
)
# Recognise himtcollege.com as an HIMT-own host too (used by _is_himt_own_page's homepage check).
HIMT_OWN_HOSTS = ("himtmarine.com", "himtcollege.com", "himt.co.in")
# SPA (JS-rendered) hosts that require Playwright to see any content.
SPA_HOSTS = ("himt.co.in", "www.himt.co.in")
# Pages that we KNOW list third-party contact info (hotels, external vendors) —
# NEVER pull facts from these.
HIMT_OWN_PAGE_DENYLIST = (
    "/himt-nearby-accommodations",
    "/industry-reviews",
    "/testimonial",
    "/press-clippings",
    "/discounts-and-offers",  # third-party partner offers
)


def _is_himt_own_page(url: str) -> bool:
    u = (url or "").lower()
    if any(d in u for d in HIMT_OWN_PAGE_DENYLIST):
        return False
    if any(a in u for a in HIMT_OWN_PAGE_ALLOWLIST):
        return True
    # Homepage counts (any HIMT-owned host, with or without trailing slash / www prefix)
    stripped = u.rstrip("/").replace("www.", "")
    return stripped.endswith(HIMT_OWN_HOSTS)


def _extract_facts_from_pages(pages: List[Tuple[str, str]]) -> Tuple[List[str], List[str], List[str], List[str]]:
    """Extract HIMT-own emails/phones/addresses ONLY from allow-listed pages.

    Args:
        pages: list of (url, text) pairs — text is the cleaned body of each page.

    Returns:
        emails, phones, addresses, source_urls (of the pages that contributed).
    """
    emails_seen = set()
    emails: List[str] = []
    phones_seen = set()
    phones: List[str] = []
    addresses_seen = set()
    addresses: List[str] = []
    source_urls: List[str] = []

    address_keywords = ["road", "street", "chennai", "vizag", "nagar", "campus", "pincode",
                        "pin code", "tamil nadu", "andhra", "visakhapatnam", "kolathur",
                        "plot no", "door no"]

    for url, text in pages:
        if not _is_himt_own_page(url):
            continue
        contributed = False

        for e in EMAIL_RE.findall(text):
            if _valid_himt_email(e) and e.lower() not in emails_seen:
                emails.append(e)
                emails_seen.add(e.lower())
                contributed = True

        for match in PHONE_RE.findall(text):
            if not _valid_phone(match):
                continue
            key = re.sub(r"\s+", " ", match.strip())
            if key in phones_seen:
                continue
            phones_seen.add(key)
            phones.append(key)
            contributed = True

        for line in text.split("\n"):
            ln = line.strip()
            if not (20 <= len(ln) <= 220):
                continue
            if any(noise.lower() in ln.lower() for noise in ["click here", "read more", "learn more", "toggle"]):
                continue
            if not any(kw in ln.lower() for kw in address_keywords):
                continue
            if ln in addresses_seen:
                continue
            addresses_seen.add(ln)
            addresses.append(ln)
            contributed = True

        if contributed:
            source_urls.append(url)

    return emails[:20], phones[:15], addresses[:10], source_urls


def _extract_facts(text: str) -> Tuple[List[str], List[str], List[str]]:
    """Backward-compat single-string extractor (kept for tests / callers that pass a blob).

    Prefer `_extract_facts_from_pages` in production so we can enforce the HIMT-own
    page allow-list.
    """
    all_emails = set(EMAIL_RE.findall(text))
    emails = sorted({e for e in all_emails if _valid_himt_email(e)})

    phones_seen = set()
    phones = []
    for match in PHONE_RE.findall(text):
        if _valid_phone(match):
            key = re.sub(r"\s+", " ", match.strip())
            if key not in phones_seen:
                phones_seen.add(key)
                phones.append(key)
    phones = phones[:15]

    addresses = []
    address_keywords = ["road", "street", "chennai", "vizag", "nagar", "campus", "pincode", "pin code", "tamil nadu", "andhra", "visakhapatnam", "kolathur", "plot no", "door no"]
    for line in text.split("\n"):
        ln = line.strip()
        if 20 <= len(ln) <= 220 and any(kw in ln.lower() for kw in address_keywords):
            if any(noise.lower() in ln.lower() for noise in ["click here", "read more", "learn more", "toggle"]):
                continue
            addresses.append(ln)
    seen = set()
    uniq_addr = []
    for a in addresses:
        if a not in seen:
            uniq_addr.append(a)
            seen.add(a)
    return emails, phones, uniq_addr[:10]


# himt.co.in exposes its full course + package catalogue as JSON via the
# online-booking backend `ob.himt.co.in`. We fetch these directly instead of
# scraping the JS-rendered Angular SPA — this gives us clean, structured fees
# and batch dates that scraping cannot reliably extract.
HIMT_CO_IN_COURSES_JSON = "https://ob.himt.co.in/jsons/All.json"
HIMT_CO_IN_PACKAGES_API = "https://ob.himt.co.in/api/V2/GetAngPackageList"
HIMT_CO_IN_ELIG_API = "https://ob.himt.co.in/api/V2/GetCourseElig_Details"
HIMT_CO_IN_BOOK_COURSES = "https://himt.co.in/Search#/Courses"
HIMT_CO_IN_BOOK_PACKAGES = "https://himt.co.in/Search#/Packages"
# himt.co.in static (server-rendered) pages we still want in the index.
HIMT_CO_IN_STATIC_PATHS = [
    "/ContactUs.html",
    "/Faq.aspx",
    "/Discounts-and-Offers.aspx",
    "/Accommodations.aspx",
    "/Enquiry_Form.aspx",
]


def _fmt_course_chunk(c: dict) -> Tuple[str, str, List[str]]:
    """Render a single course dict from All.json into (chunk_text, page_title, intent_tags)."""
    course_name = (c.get("Course") or "").strip()
    code = (c.get("MinorCode") or "").strip()
    category = (c.get("MajorCode") or "").strip()
    fees = (c.get("Fees") or "").strip()
    location = (c.get("Location") or "").strip()
    duration = (c.get("Duration") or "").strip()
    approved = (c.get("ApprovedBy") or "").strip()
    entity_code = (c.get("EntityCode") or "").strip()
    dmt = (c.get("DayMonthType") or "").strip()
    batches = c.get("Batches") or []

    lines: List[str] = [f"Course: {course_name}"]
    if code:
        lines.append(f"Course Code: {code}")
    if category:
        lines.append(f"Category: {category.title()}")
    if fees:
        lines.append(
            f"Course Fees: INR {fees} (Rs. {fees}) — listed on HIMT Online Booking "
            f"(himt.co.in)"
        )
    if duration:
        unit = "Months" if dmt.upper() == "M" else "Days"
        lines.append(f"Duration: {duration} {unit}")
    if location:
        lines.append(f"Location: {location.title()}")
    if entity_code:
        lines.append(f"Booking Entity: {entity_code}")
    if approved:
        appr = {"DG": "DG Shipping", "MCA": "UK MCA"}.get(approved.upper(), approved)
        lines.append(f"Approved By: {appr}")

    if batches:
        lines.append("Upcoming Batches (from HIMT Online Booking):")
        for b in batches[:6]:
            bd = (b.get("CourseDate") or "").strip()
            ed = (b.get("EndDate") or "").strip()
            bf = (b.get("Fees") or "").strip()
            bloc = (b.get("Location") or "").strip()
            st = (b.get("StartTime") or "").strip()
            et = (b.get("EndTime") or "").strip()
            part = f"- {bd}"
            if ed and ed != bd:
                part += f" to {ed}"
            if st and et:
                part += f" ({st}-{et})"
            if bf and bf != fees:
                part += f" — Fees INR {bf}"
            if bloc and bloc.lower() != location.lower():
                part += f" — {bloc.title()}"
            lines.append(part)

    lines.append(f"Book online: {HIMT_CO_IN_BOOK_COURSES}")

    intents = ["courses", "admissions"]
    title = f"{course_name} — HIMT Online Booking"
    return "\n".join(lines), title, intents


def _fmt_package_chunk(pk: dict) -> Tuple[str, str, List[str]]:
    """Render a package dict from GetAngPackageList into (chunk_text, title, intents)."""
    pname = (pk.get("Pkg_Name") or "").strip()
    lines: List[str] = [f"Package: {pname}"]
    pid = (pk.get("Pkg_ID") or "").strip()
    if pid:
        lines.append(f"Package ID: {pid}")
    fees2 = pk.get("Pkg_Fees2") or pk.get("Pkg_Fees")
    if fees2 and str(fees2).strip() not in ("", "-"):
        lines.append(f"Package Fees: INR {str(fees2).strip()}")
    fd = pk.get("FeesDiscount")
    try:
        if fd and float(fd) > 0:
            lines.append(f"Discount vs individual booking: INR {float(fd):.0f}")
    except (TypeError, ValueError):
        pass
    sums = pk.get("CoursesSeperateFeesSum")
    if sums:
        try:
            if fees2 and float(fees2) > 0 and float(sums) > float(fees2):
                saving = float(sums) - float(fees2)
                lines.append(
                    f"Buying courses individually would cost INR {float(sums):.0f} — "
                    f"package saves INR {saving:.0f}"
                )
        except (TypeError, ValueError):
            pass
    sd = (pk.get("Start_Date") or "").strip()
    if sd:
        lines.append(f"Next Start Date: {sd}")
    ent = (pk.get("EntityShortCode") or "").strip()
    if ent:
        lines.append(f"Booking Entity: {ent}")
    codes = (pk.get("CourseCodes") or "").strip()
    if codes:
        # Collapse Oracle-style padded course-code strings into a compact list.
        parts = [p.strip() for p in codes.split(",") if p.strip()]
        if parts:
            lines.append(f"Includes courses: {', '.join(parts)}")
    lines.append(f"Book online: {HIMT_CO_IN_BOOK_PACKAGES}")

    return "\n".join(lines), f"{pname} — HIMT Package", ["courses", "admissions"]


_WS_RX = re.compile(r"[ \t\r\n\u00a0]+")
_HTML_TAG_RX = re.compile(r"<[^>]+>")


def _clean_api_text(s: str) -> str:
    """The GetCourseElig_Details API returns strings with `\\r`-separated bullets
    and occasional inline HTML (e.g. `<a>`, `<br>`). Normalise to lines."""
    if not s:
        return ""
    s = _HTML_TAG_RX.sub(" ", s)
    # Bulletise carriage-returns / repeated whitespace into newlines.
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.strip() for ln in s.split("\n")]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def _fmt_docs_chunk(course_name: str, code: str, elig: dict) -> Tuple[str, str, List[str]]:
    """Render the docs + eligibility payload for ONE course into a chunk."""
    docs = _clean_api_text(elig.get("Document_Details") or "")
    eligibility = _clean_api_text(elig.get("eligibility") or "")
    validity = (elig.get("CourseValidity") or "").strip()
    exit_exam = (elig.get("ExitExam") or "").strip()
    exit_fee = (elig.get("ExitExamFees") or "").strip()
    phys_days = (elig.get("PhysicalDay") or "").strip()
    phys_details = _clean_api_text(elig.get("PhysicalDayDetails") or "")
    elearning = (elig.get("Elearning") or "").strip()

    lines: List[str] = [f"Course: {course_name} (Code: {code})"]
    if docs:
        lines.append("Documents required for booking (as listed on HIMT Online Booking):")
        for ln in docs.splitlines():
            lines.append(f"- {ln}")
    if eligibility:
        lines.append("Eligibility:")
        for ln in eligibility.splitlines():
            lines.append(f"- {ln}")
    if validity:
        lines.append(f"Course Validity: {validity}")
    if phys_days:
        lines.append(f"Physical Class Days: {phys_days}")
    if phys_details:
        lines.append(f"Physical Class Details: {phys_details}")
    if elearning and elearning.lower() not in ("no", "n"):
        lines.append(f"E-Learning: {elearning}")
    if exit_exam and exit_exam.lower() not in ("no", "n"):
        exam_line = f"Exit Exam: {exit_exam}"
        if exit_fee:
            exam_line += f" (Fee: INR {exit_fee})"
        lines.append(exam_line)

    lines.append(f"Book online: {HIMT_CO_IN_BOOK_COURSES}")

    intents = ["courses", "admissions", "documents"]
    title = f"{course_name} — Documents & Eligibility (HIMT Online Booking)"
    return "\n".join(lines), title, intents


async def _fetch_course_docs(client: httpx.AsyncClient, minor_codes: List[str]) -> Dict[str, dict]:
    """Batch-fetch documents + eligibility for a list of course MinorCodes.

    Returns a dict keyed by Cmn_Minor_code. The endpoint accepts up to ~100
    codes per POST, so we chunk into batches of 60 to stay well under any
    server-side cap.
    """
    out: Dict[str, dict] = {}
    codes = [c for c in minor_codes if c]
    BATCH = 60
    for i in range(0, len(codes), BATCH):
        batch = codes[i:i + BATCH]
        try:
            r = await client.post(
                HIMT_CO_IN_ELIG_API,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                content=f"MinorCodes={','.join(batch)}",
            )
            r.raise_for_status()
            for item in (r.json() or {}).get("Result") or []:
                k = (item.get("Cmn_Minor_code") or "").strip()
                if k:
                    out[k] = item
        except Exception:
            # Best-effort: caller will log via `errors` list.
            continue
    return out


async def crawl_himt_co_in(base_url: str, db) -> Dict:
    """Ingest himt.co.in via its JSON APIs (courses, packages) + static HTML pages.

    We deliberately do NOT scrape the Angular SPA — its course cards hide fees
    behind an interactive "Select Date" dropdown, and BeautifulSoup ends up
    emitting labels without values. The site's own booking backend
    (`ob.himt.co.in`) already exposes fully-structured data as JSON, so we use
    that as the source of truth for fees + batch dates.
    """
    base_url = base_url.rstrip("/")
    pages_crawled = 0
    chunks_created = 0
    errors: List[str] = []
    facts_input: List[Tuple[str, str]] = []

    headers = {
        "User-Agent": "HIMTChatbotIngestion/1.0 (+https://himtmarine.com)",
        "Accept": "application/json, text/html;q=0.9, */*;q=0.5",
    }
    async with httpx.AsyncClient(headers=headers, timeout=60.0) as client:
        # 1) Static server-rendered pages (contact/faq/etc.) via httpx.
        for p in HIMT_CO_IN_STATIC_PATHS:
            u = f"{base_url}{p}"
            try:
                r = await client.get(u, follow_redirects=True)
                r.raise_for_status()
                title, text = _extract_main_text(r.text)
                if not text or len(text) < 60:
                    continue
                await db.pages.insert_one(
                    CrawledPage(url=u, title=title, raw_text=text[:20000], status="ok").model_dump()
                )
                pages_crawled += 1
                facts_input.append((u, text))
                path_lower = p.lower()
                for chunk_text in _chunk_text(text):
                    intents = _detect_intents(chunk_text)
                    if "contact" in path_lower and "contact" not in intents:
                        intents.append("contact")
                    if "faq" in path_lower:
                        for extra in ("admissions", "courses"):
                            if extra not in intents:
                                intents.append(extra)
                    await db.chunks.insert_one(Chunk(
                        page_url=u, page_title=title, text=chunk_text,
                        tokens=len(chunk_text.split()), intent_tags=intents,
                    ).model_dump())
                    chunks_created += 1
            except Exception as e:
                errors.append(f"{u}: {e}")

        # 2) Full course catalogue via JSON.
        try:
            r = await client.get(HIMT_CO_IN_COURSES_JSON)
            r.raise_for_status()
            courses = (r.json() or {}).get("Result", []) or []
        except Exception as e:
            errors.append(f"himt.co.in All.json: {e}")
            courses = []

        if courses:
            await db.pages.insert_one(CrawledPage(
                url=HIMT_CO_IN_BOOK_COURSES,
                title="HIMT Online Booking — Courses & Fees",
                raw_text=f"HIMT Online Booking course catalogue with {len(courses)} courses.",
                status="ok",
            ).model_dump())
            pages_crawled += 1

        for c in courses:
            if not (c.get("Course") or "").strip():
                continue
            chunk_text, title, intents = _fmt_course_chunk(c)
            await db.chunks.insert_one(Chunk(
                page_url=HIMT_CO_IN_BOOK_COURSES,
                page_title=title, text=chunk_text,
                tokens=len(chunk_text.split()), intent_tags=intents,
            ).model_dump())
            chunks_created += 1

        # 2b) Per-course documents + eligibility via GetCourseElig_Details.
        # Each course card on himt.co.in exposes a "Documents" button that
        # pulls this endpoint — the same content, in structured form.
        try:
            elig_map = await _fetch_course_docs(
                client,
                [(c.get("MinorCode") or "").strip() for c in courses],
            )
        except Exception as e:
            errors.append(f"GetCourseElig_Details: {e}")
            elig_map = {}
        for c in courses:
            code = (c.get("MinorCode") or "").strip()
            elig = elig_map.get(code)
            if not elig:
                continue
            if not (elig.get("Document_Details") or elig.get("eligibility")):
                continue
            course_name = (c.get("Course") or "").strip()
            chunk_text, title, intents = _fmt_docs_chunk(course_name, code, elig)
            await db.chunks.insert_one(Chunk(
                page_url=HIMT_CO_IN_BOOK_COURSES,
                page_title=title, text=chunk_text,
                tokens=len(chunk_text.split()), intent_tags=intents,
            ).model_dump())
            chunks_created += 1

        # 3) Packages via JSON API.
        try:
            r = await client.post(
                HIMT_CO_IN_PACKAGES_API,
                headers={"Content-Type": "application/json"},
                content="{}",
            )
            r.raise_for_status()
            packages = (r.json() or {}).get("Result", []) or []
        except Exception as e:
            errors.append(f"GetAngPackageList: {e}")
            packages = []

        if packages:
            await db.pages.insert_one(CrawledPage(
                url=HIMT_CO_IN_BOOK_PACKAGES,
                title="HIMT Online Booking — Packages",
                raw_text=f"HIMT package catalogue with {len(packages)} packages.",
                status="ok",
            ).model_dump())
            pages_crawled += 1

        for pk in packages:
            if not (pk.get("Pkg_Name") or "").strip():
                continue
            chunk_text, title, intents = _fmt_package_chunk(pk)
            await db.chunks.insert_one(Chunk(
                page_url=HIMT_CO_IN_BOOK_PACKAGES,
                page_title=title, text=chunk_text,
                tokens=len(chunk_text.split()), intent_tags=intents,
            ).model_dump())
            chunks_created += 1

    return {
        "pages_crawled": pages_crawled,
        "chunks_created": chunks_created,
        "errors": errors,
        "facts_input": facts_input,
    }


async def _fetch(client: httpx.AsyncClient, url: str) -> Tuple[str, str, str]:
    """Return (url, html, error)."""
    try:
        r = await client.get(url, timeout=20.0, follow_redirects=True)
        r.raise_for_status()
        return url, r.text, ""
    except Exception as e:
        return url, "", str(e)


PRIORITY_PATHS_BY_HOST = {
    "himtmarine.com": PRIORITY_PATHS,
    "www.himtmarine.com": PRIORITY_PATHS,
    # Pre-sea courses live on himtcollege.com — crawl its high-priority pages
    # PLUS the individual course detail pages under /himt_courses/*.
    "himtcollege.com": [
        "/",
        "/course/",
        "/bsm-eto/",
        "/bsm-gme/",
        "/imec-gme/",
        "/join-merchant-navy/",
        "/placement/",
        "/faq/",
        "/discount-for-female-candidates/",
        "/educational-loan/",
        "/e-brochure/",
        "/testimonial/",
        "/contact/",
        "/careers/",
        # Individual dedicated course detail pages (used for "Learn more" per-course URLs).
        "/himt_courses/bsc-nautical-science-3-years/",
        "/himt_courses/btech-marine-engineering/",
        "/himt_courses/btech-marine-engineering-lateral-entry/",
        "/himt_courses/diploma-in-nautical-science/",
        "/himt_courses/electro-technical-officers-eto-4-months/",
        "/himt_courses/graduate-marine-engineering-gme/",
        "/himt_courses/join-merchant-navy-course-general-purpose-rating/",
        "/himt_courses/orientation-course-for-catering-personnel/",
    ],
    "www.himtcollege.com": [
        "/", "/course/",
        "/himt_courses/bsc-nautical-science-3-years/",
        "/himt_courses/btech-marine-engineering/",
        "/himt_courses/btech-marine-engineering-lateral-entry/",
        "/himt_courses/diploma-in-nautical-science/",
        "/himt_courses/electro-technical-officers-eto-4-months/",
        "/himt_courses/graduate-marine-engineering-gme/",
        "/himt_courses/join-merchant-navy-course-general-purpose-rating/",
        "/himt_courses/orientation-course-for-catering-personnel/",
    ],
    # himt.co.in is a JS-rendered SPA — crawled via Playwright with hash routes.
    "himt.co.in": [
        "/Search#/Home",
        "/Search#/Courses",
        "/Search#/Courses?val=REUC",
        "/Search#/Courses?val=VIZ",
        "/Search#/Packages",
        "/ContactUs.html",
        "/Faq.aspx",
        "/Discounts-and-Offers.aspx",
        "/Accommodations.aspx",
        "/Enquiry_Form.aspx",
    ],
}


async def _crawl_single_site(base_url: str, db, extra_paths: List[str] = None) -> Dict:
    """Crawl a single base URL, returning per-site counts and page (url, text) tuples.
    Uses Playwright for JS-rendered SPA hosts (SPA_HOSTS), httpx otherwise.
    Also inserts pages + chunks into db. Facts are collected but not persisted here.
    """
    base_url = base_url.rstrip("/")
    parsed_base = urlparse(base_url)
    domain = parsed_base.netloc
    is_spa = domain in SPA_HOSTS or domain.replace("www.", "") in SPA_HOSTS

    paths = list(PRIORITY_PATHS_BY_HOST.get(domain, PRIORITY_PATHS))
    if extra_paths:
        for p in extra_paths:
            if p not in paths:
                paths.append(p)

    urls: Set[str] = {urljoin(base_url + "/", p.lstrip("/")) for p in paths}

    if is_spa:
        # SPA hosts (himt.co.in) — served by an Angular app whose course cards
        # hide fees + batch dates until interacted with. We fetch the same data
        # the SPA fetches (JSON APIs on ob.himt.co.in) plus its static
        # server-rendered pages via httpx.
        return await crawl_himt_co_in(base_url, db)
    else:
        headers = {
            "User-Agent": "HIMTChatbotIngestion/1.0 (+https://himtmarine.com)",
            "Accept": "text/html,application/xhtml+xml",
        }
        pages_crawled = 0
        chunks_created = 0
        errors: List[str] = []
        all_text_for_facts: List[Tuple[str, str]] = []

        async with httpx.AsyncClient(headers=headers) as client:
            home_url = base_url + "/"
            home_url_final, home_html, home_err = await _fetch(client, home_url)
            if home_html:
                soup = BeautifulSoup(home_html, "lxml")
                bare_host = domain.replace("www.", "")
                allowed_hosts = {bare_host, "www." + bare_host}
                for a in soup.find_all("a", href=True):
                    href = a["href"].strip()
                    if href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
                        continue
                    absolute = urljoin(home_url, href)
                    if urlparse(absolute).netloc in allowed_hosts:
                        clean = absolute.split("#")[0].split("?")[0]
                        urls.add(clean)

            urls_list = sorted(urls)[:60]
            sem = asyncio.Semaphore(6)

            async def bounded_fetch(u):
                async with sem:
                    return await _fetch(client, u)

            results = await asyncio.gather(*[bounded_fetch(u) for u in urls_list])

    for url, html, err in results:
        if err or not html:
            errors.append(f"{url}: {err}")
            page_doc = CrawledPage(url=url, status="failed", error=err).model_dump()
            await db.pages.insert_one(page_doc)
            continue

        try:
            title, text = _extract_main_text(html)
            if not text or len(text) < 60:
                errors.append(f"{url}: empty content")
                continue

            page = CrawledPage(url=url, title=title, raw_text=text[:20000], status="ok")
            await db.pages.insert_one(page.model_dump())
            pages_crawled += 1
            all_text_for_facts.append((url, text))

            for chunk_text in _chunk_text(text):
                intents = _detect_intents(chunk_text)
                path_lower = urlparse(url).path.lower()
                if "contact" in path_lower or "about" in path_lower:
                    if "about" not in intents:
                        intents.append("about")
                if "course" in path_lower or "modular" in path_lower or "package" in path_lower or "mca" in path_lower:
                    if "courses" not in intents:
                        intents.append("courses")
                if "booking" in path_lower or "submission" in path_lower or "admission" in path_lower:
                    if "admissions" not in intents:
                        intents.append("admissions")
                # Pre-sea specific intent tagging (himtcollege.com pages)
                if any(p in path_lower for p in ("/bsm-", "/imec-", "/join-merchant-navy", "/pre-sea", "/course")):
                    if "presea" not in intents:
                        intents.append("presea")
                    if "courses" not in intents:
                        intents.append("courses")

                chunk = Chunk(
                    page_url=url,
                    page_title=title,
                    text=chunk_text,
                    tokens=len(chunk_text.split()),
                    intent_tags=intents,
                )
                await db.chunks.insert_one(chunk.model_dump())
                chunks_created += 1

        except Exception as e:
            errors.append(f"{url}: parse error {e}")

    return {
        "pages_crawled": pages_crawled,
        "chunks_created": chunks_created,
        "errors": errors,
        "facts_input": all_text_for_facts,
    }


async def crawl_and_ingest(base_url: str, db, run_id: str, extra_paths: List[str] = None,
                           additional_sites: List[str] = None) -> Dict:
    """Crawl one or more HIMT sites, then chunk & store.

    `base_url` is the primary site. `additional_sites` is an optional list of sibling HIMT
    domains (e.g. himtcollege.com for pre-sea courses).
    """
    # Clear old data first
    await db.chunks.delete_many({})
    await db.pages.delete_many({})

    sites = [base_url]
    for s in additional_sites or []:
        s = s.strip()
        if s and s not in sites:
            sites.append(s)

    pages_crawled = 0
    chunks_created = 0
    errors: List[str] = []
    all_facts_input: List[Tuple[str, str]] = []

    for site in sites:
        res = await _crawl_single_site(site, db, extra_paths=extra_paths if site == base_url else None)
        pages_crawled += res["pages_crawled"]
        chunks_created += res["chunks_created"]
        errors.extend(res["errors"])
        all_facts_input.extend(res["facts_input"])

    # Structured facts — restricted to HIMT-own pages via allow-list.
    emails, phones, addresses, fact_pages_used = _extract_facts_from_pages(all_facts_input)
    facts = StructuredFacts(
        emails=emails,
        phones=phones,
        addresses=addresses,
        source_urls=fact_pages_used,
    )
    await db.structured_facts.delete_many({})
    await db.structured_facts.insert_one(facts.model_dump())

    return {
        "pages_crawled": pages_crawled,
        "chunks_created": chunks_created,
        "errors": errors,
        "emails_found": len(emails),
        "phones_found": len(phones),
        "addresses_found": len(addresses),
        "sites": sites,
    }
