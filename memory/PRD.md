# HIMT AI Chatbot — PRD

## Problem Statement
Production-oriented AI chatbot embedded on the HIMT websites (himtmarine.com, himtcollege.com, himt.co.in) that gives accurate, concise answers using ONLY official website content. Focus on reliability via retrieval, monitoring, and graceful fallback.

## User Personas
- Prospective students (pre-sea / post-sea)
- Parents / guardians researching maritime training
- Website visitors comparing courses / fees / campuses
- Existing students verifying admission / booking procedures

## Architecture
- **Backend**: FastAPI + MongoDB + Anthropic Claude API (claude-opus-5-5)
- **Frontend**: React + Tailwind + Shadcn UI + Framer Motion
- **RAG pipeline**: crawl → clean → chunk → intent-tag → keyword+URL-boosted ranking → grounded LLM answer
- **Anthropic API key (ANTHROPIC_API_KEY)**

## Embed on himtmarine.com
Snippet placed just before `</body>`:
```html
<script src="https://<preview>/embed.js" async></script>
```
- `/embed.js` — auto-injects an iframe fixed bottom-right
- `/embed-demo.html` — preview page simulating himtmarine.com with the widget

## Core Requirements — Implemented
- ✅ Crawl HIMT pages (himtmarine.com + himtcollege.com + himt.co.in), chunk, dedupe, clean nav/footer noise (84 pages, 1117 chunks as of Aug 2026)
- ✅ Alias handling: HIMT ↔ Hindustan Institute of Maritime Training
- ✅ Structured extraction of contact facts (emails/phones/addresses) with HIMT-domain filter
- ✅ Chunk-level retrieval with intent-aware URL-canonical boosting (courses/about/admissions/contact/presea/documents/app)
- ✅ Grounded Claude answers via the anthropic SDK
- ✅ Source-linked answers (up to 4 unique HIMT URLs per response) with HOST DIVERSITY
- ✅ Graceful fallback with rule-based response when the Claude call fails
- ✅ Chat log persistence, SHA256 cache
- ✅ Admin console at /admin (token-gated): re-ingest, stats/runs/facts/logs
- ✅ Auto-ingestion on first boot; manual re-ingest via admin
- ✅ Floating chat widget, suggested prompts, source chips, status badges
- ✅ Lead Capture on admissions intent; admin can view + mark contacted/closed
- ✅ Daily scheduled re-ingestion (APScheduler cron at 02:00 UTC)
- ✅ Copy-Answer button + thumbs-up/down feedback (persisted in `feedback` collection)
- ✅ Weak-Answer alerts via Resend (blocked pending himt.org DNS verification)
- ✅ Pre-sea course ingestion (himtcollege.com) with dedicated per-course "Learn more" URLs
- ✅ Documents-required intent asks clarifying pre-sea vs post-sea question
- ✅ App-details intent returns Play Store + App Store URLs

## Feb 2026 — himt.co.in SPA fee-extraction fix (P0)
- ✅ Replaced Playwright DOM scraping (which lost fee→course association behind the "Select Date" dropdown) with a direct call to HIMT's own booking-portal JSON APIs:
  - `https://ob.himt.co.in/jsons/All.json` — full course catalogue (99 courses) with fees, batches, duration, location, INDoS flag, DG/UK-MCA approval
  - `POST https://ob.himt.co.in/api/V2/GetAngPackageList` — package catalogue (38 packages) with package fees, discount vs individual booking, next start dates
  - `https://himt.co.in/ContactUs.html`, `/Faq.aspx`, `/Discounts-and-Offers.aspx`, `/Accommodations.aspx` still fetched as static HTML via httpx
- ✅ One structured chunk emitted per course/package (course name + code + category + fees + duration + location + entity + approval + upcoming batches with start/end date + fees per batch + seats + timings). Retrieval now returns exact PSCRB / STCW / GMDSS / package fees.
- ✅ New `crawl_himt_co_in()` in `backend/ingestion.py`; `_crawl_single_site()` delegates SPA hosts to it. Playwright no longer required for the primary flow.

## Feb 2026 — 6-hourly himt.co.in refresh (P1)
- ✅ New `_scheduled_himt_co_in_refresh()` in `backend/server.py` deletes only himt.co.in chunks + pages, re-crawls via `crawl_himt_co_in()`, records an ingestion_runs entry with trigger='himt_co_in_refresh'.
- ✅ APScheduler CronTrigger(hour='*/6', minute=30) registered as job id 'himt-co-in-refresh'. `/api/admin/schedule` returns both `daily-ingest` (02:00 UTC) and `himt-co-in-refresh` (every 6 h).
- ✅ 90-minute skip guard prevents overlap with the daily full-ingestion run.

## Feb 2026 — Source diversity fix
- ✅ `retrieval.retrieve()` now returns top_k top-scored chunks PLUS additional unique-URL chunks up to `source_urls_cap=12`.
- ✅ Chat endpoint builds sources with host diversity (one URL per host first, then fill by rank) so course queries surface himt.co.in (fees/batches) AND himtmarine.com/himtcollege.com (descriptions) side-by-side.
- ✅ LLM context capped at 2 chunks per URL so descriptive canonical pages appear alongside booking chunks.
- ✅ `retrieval.INTENT_MAP.courses` now includes 'package', 'packages', 'combo', 'bundle'.

## Feb 2026 — Topic-owner map + clickable hyperlinks
- ✅ `retrieval.owner_hosts()` maps high-stakes queries to a single owner host and hard-filters other sites out of the ranking:
  - Fees / prices / cost / charges queries → himt.co.in only
  - Batch / schedule / start dates queries → himt.co.in only
  - Pre-sea course queries → himtcollege.com only
  - Other intents keep their existing soft URL-boost behaviour
- ✅ Fallback: if the hard filter would empty results, unfiltered ranking is used so the user still gets an answer instead of a canned refusal.
- ✅ System-prompt rule 15 now instructs the LLM to emit URLs as markdown `[label](https://…)` (never bare) and to always include a `Learn more:` link (+ `Book online:` for fee/booking answers, `Contact HIMT:` for contact answers).
- ✅ Frontend `ChatWidget` now linkifies markdown links AND bare URLs, renders `**bold**` and bullet lines, and shows a **Sources** row of pill-style clickable chips beneath every assistant answer (`data-testid="answer-link"`, `source-chip-N`, `answer-sources`).
- ✅ Copy-Answer flattens markdown links to `label (url)` so pasted plain text stays readable, and appends a Sources list to the copied block.
- ✅ Per-URL cap in the LLM context now canonicalises URLs (strips `www.` + trailing slash) so a page crawled under both `himtcollege.com/contact/` and `www.himtcollege.com/contact/` counts as ONE URL and doesn't crowd out the campus/address chunk.

## Feb 2026 — Per-course required documents from himt.co.in
- ✅ New endpoint wired: `POST https://ob.himt.co.in/api/V2/GetCourseElig_Details` (form-encoded `MinorCodes=CODE1,CODE2,…`) — the same API the booking portal's "Documents" button calls.
- ✅ `crawl_himt_co_in()` now batch-fetches (60 codes/POST) `Document_Details` + `eligibility` + `CourseValidity` + `PhysicalDay` + `ExitExam` for every course and emits a dedicated docs chunk per course (tagged `documents`, `courses`, `admissions`). Added ~98 per-course docs chunks (index size 1117 → 1215).
- ✅ SYSTEM_PROMPT rule 12 updated: when the user names a course, the LLM prefers the himt.co.in per-course docs block over the generic himtmarine.com FAQ.
- ✅ `INTENT_URL_PATTERNS["documents"]` now boosts `himt.co.in/search#/courses`.
- ✅ Per-URL LLM-context cap raised to **4** for `himt.co.in/Search#/{Courses,Packages}` (they're the single URL that carries one chunk per course), so a course-specific query can actually see that course's chunk in the top-5.
- ✅ Light plural stemming in retrieval (`fees→fee`, `batches→batch`, `documents→document`, `required→require`) plus mongo-regex suffix pattern so a "fee" token still matches "fees" in chunks.
- ✅ In-flight guard on `POST /api/admin/reingest` prevents overlapping runs (was silently doubling chunks when the button was clicked twice or scheduler and API overlapped).

## Testing (Feb 2026 — after per-course docs)
- Backend pytest suite: **120/125 stable** (2 skipped, 3 LLM-output flakes that pass in isolation; `test_weak_alert.py` excluded — Resend domain verification blocked on user action).
- New suite: `test_per_course_docs.py`.
- Manual verification: "Documents required for PSCRB / AFF / GMDSS" now return the exact document lists from the booking portal, with DGS vs UK MCA variants disambiguated.

## Feb 2026 — Pre-sea answers no longer show `Book online:` link
- ✅ System-prompt rules 12, 14 and the COURSE URL MAP rule 3 now forbid `Book online:` on any pre-sea programme (B.Sc / B.Tech / DNS / GME / ETO / GP Rating / OCCP / BSm / IMEC). Pre-sea admissions happen via the college's admission process, not the online booking portal.
- ✅ Post-sea fee / batch / package answers still emit both `Learn more:` and `Book online: https://himt.co.in/…`.
- ✅ New suite `test_presea_no_book_online.py` asserts the boundary (6 pre-sea queries must NOT include "book online"; 3 post-sea queries must include it).
- Backend pytest suite: **132 passed, 2 skipped, 2 known LLM flakes** (`test_weak_alert.py` excluded — Resend domain verification still blocked).

## Feb 2026 — Boot self-heal for production resiliency
- ✅ `startup_event` now fires a full ingestion when any of these is true:
  1. `chunks == 0` (first-ever boot)
  2. `himt.co.in` chunks are missing (older index predates the JSON crawler)
  3. `chunks > 1500` OR `pages > 120` (bloat from an earlier double-ingest)
- ✅ `POST /api/admin/reingest` has an in-flight guard + a 20-second throttle so overlapping clicks / scheduler collisions can no longer double the index.

## Prioritised Backlog
- P1 — BLOCKED on user: Verify `himt.org` domain in Resend, set `RESEND_FROM_EMAIL=alerts@himt.org` so weak-answer alerts deliver to HNP-IT@himt.org.
- P2 — Vector embeddings (OpenAI text-embedding-3-small) for higher retrieval precision on paraphrased queries.
- P2 — Multilingual support (Hindi / Tamil).
- P2 — WhatsApp handoff button.
- P3 — Streaming responses (SSE) for perceived speed.

## Next Tasks
- User to verify Resend domain → then update `RESEND_FROM_EMAIL` and confirm alert delivery.
- Optional: expose a `POST /api/admin/refresh-himt-co-in` endpoint so admins can trigger the 6-hourly refresh on demand between crons.
