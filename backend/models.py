"""Pydantic models for HIMT chatbot."""
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
import uuid


def _uuid() -> str:
    return str(uuid.uuid4())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CrawledPage(BaseModel):
    id: str = Field(default_factory=_uuid)
    url: str
    title: str = ""
    raw_text: str = ""
    fetched_at: str = Field(default_factory=_now_iso)
    status: str = "ok"  # ok / failed
    error: Optional[str] = None


class Chunk(BaseModel):
    id: str = Field(default_factory=_uuid)
    page_url: str
    page_title: str
    text: str
    tokens: int = 0  # approx word count
    intent_tags: List[str] = Field(default_factory=list)  # about, courses, admissions, contact
    created_at: str = Field(default_factory=_now_iso)


class StructuredFacts(BaseModel):
    """Structured extraction of high-value contact info."""
    id: str = Field(default_factory=_uuid)
    emails: List[str] = Field(default_factory=list)
    phones: List[str] = Field(default_factory=list)
    addresses: List[str] = Field(default_factory=list)
    source_urls: List[str] = Field(default_factory=list)
    updated_at: str = Field(default_factory=_now_iso)


class IngestionRun(BaseModel):
    id: str = Field(default_factory=_uuid)
    started_at: str = Field(default_factory=_now_iso)
    finished_at: Optional[str] = None
    status: str = "running"  # running / completed / failed
    pages_crawled: int = 0
    chunks_created: int = 0
    errors: List[str] = Field(default_factory=list)


class ChatMessage(BaseModel):
    role: str  # user | assistant
    content: str


class ChatRequest(BaseModel):
    session_id: Optional[str] = None
    message: str


class SourceRef(BaseModel):
    url: str
    title: str


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    sources: List[SourceRef] = Field(default_factory=list)
    status: str  # grounded | fallback | insufficient | error
    cached: bool = False
    latency_ms: int = 0
    intent: str = "general"
    offer_lead_capture: bool = False
    log_id: Optional[str] = None


class ChatLog(BaseModel):
    id: str = Field(default_factory=_uuid)
    session_id: str
    query: str
    answer: str
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    status: str  # grounded | fallback | insufficient | error
    intent: str = "general"
    latency_ms: int = 0
    cached: bool = False
    created_at: str = Field(default_factory=_now_iso)


class AdminAuthRequest(BaseModel):
    token: str


class Lead(BaseModel):
    id: str = Field(default_factory=_uuid)
    name: str
    phone: str
    email: Optional[str] = None
    interest: Optional[str] = None  # course of interest or free text
    session_id: Optional[str] = None
    source_query: Optional[str] = None  # the chat question that triggered capture
    status: str = "new"  # new / contacted / closed
    created_at: str = Field(default_factory=_now_iso)


class LeadCreate(BaseModel):
    name: str
    phone: str
    email: Optional[str] = None
    interest: Optional[str] = None
    session_id: Optional[str] = None
    source_query: Optional[str] = None


class Feedback(BaseModel):
    id: str = Field(default_factory=_uuid)
    log_id: Optional[str] = None
    session_id: Optional[str] = None
    query: Optional[str] = None
    answer_preview: Optional[str] = None
    sentiment: str  # "up" | "down"
    intent: Optional[str] = None
    status: Optional[str] = None  # grounded / insufficient / fallback / error at feedback time
    created_at: str = Field(default_factory=_now_iso)


class FeedbackCreate(BaseModel):
    log_id: Optional[str] = None
    session_id: Optional[str] = None
    query: Optional[str] = None
    answer_preview: Optional[str] = None
    sentiment: str  # "up" | "down"
    intent: Optional[str] = None
    status: Optional[str] = None
