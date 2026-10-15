"""Pydantic models for every data type.

The six models in section 5 of the spec (Chunk, Citation, BriefField, BidItem,
RiskFlag, Answer) are kept as written. The only addition to a spec model is
`Chunk.char_start/char_end`, which section F1 requires ("every chunk keeps ...
character offsets"). Everything else here is a wrapper around the spec models.

Rule: the model never sets `Citation.verified`. Model output uses `LLMCitation`
(no `verified` field at all); code converts it to `Citation` after checking.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RISK_CATEGORIES: list[str] = [
    "Schedule",
    "Liquidated damages",
    "Payment",
    "Bonds and insurance",
    "Site conditions",
    "Traffic",
    "Testing",
    "Responsibility shifts",
    "Labor",
]

BRIEF_FIELDS: list[tuple[str, str]] = [
    ("project_name", "Project name"),
    ("owner", "Owner"),
    ("location", "Location"),
    ("scope", "Scope"),
    ("bid_due", "Bid due"),
    ("pre_bid_meeting", "Pre-bid meeting"),
    ("completion_time", "Completion time"),
    ("bid_security", "Bid security"),
    ("bonds", "Bonds"),
]


# ---------------------------------------------------------------- spec models
class Chunk(BaseModel):
    chunk_id: str
    file: str
    page: int
    section: str | None
    text: str
    is_ocr: bool
    char_start: int = 0  # offset of this chunk inside the page text
    char_end: int = 0


class Citation(BaseModel):
    file: str
    page: int
    quote: str  # copied exactly from the page
    verified: bool = False  # set by code, never by the model


class BriefField(BaseModel):
    name: str  # e.g. "bid_due", "completion_time"
    value: str | None  # None means "Not found in the documents"
    citations: list[Citation] = Field(default_factory=list)


class BidItem(BaseModel):
    item_no: str
    description: str
    quantity: str  # kept as text, exactly as printed
    unit: str
    page: int
    confidence: float  # below 0.8 is highlighted for review
    file: str = ""
    note: str = ""


class RiskFlag(BaseModel):
    category: str  # one of the nine categories
    reason: str  # one plain-English line
    severity: Literal["High", "Medium", "Low"]
    citations: list[Citation] = Field(default_factory=list)


class Answer(BaseModel):
    text: str
    found: bool
    citations: list[Citation] = Field(default_factory=list)


# ------------------------------------------------- model-facing (LLM) shapes
class LLMCitation(BaseModel):
    file: str = ""
    page: int
    quote: str


class LLMBriefField(BaseModel):
    name: str
    value: str | None = None
    citations: list[LLMCitation] = Field(default_factory=list)


class LLMBrief(BaseModel):
    fields: list[LLMBriefField]


class LLMRiskFlag(BaseModel):
    category: str
    reason: str
    severity: Literal["High", "Medium", "Low"]
    citations: list[LLMCitation] = Field(default_factory=list)


class LLMRisks(BaseModel):
    risks: list[LLMRiskFlag] = Field(default_factory=list)


class LLMAnswer(BaseModel):
    text: str
    found: bool
    citations: list[LLMCitation] = Field(default_factory=list)


class LLMTableRow(BaseModel):
    item_no: str
    description: str
    quantity: str
    unit: str


class LLMTable(BaseModel):
    rows: list[LLMTableRow] = Field(default_factory=list)


CHANGE_KINDS: list[str] = [
    "Bid date or time",
    "Bid items or quantities",
    "Scope of work",
    "Schedule",
    "Requirements",
    "Drawings",
    "Answers to questions",
    "Other",
]


class LLMChange(BaseModel):
    addendum: str
    kind: str
    summary: str
    affects: str = "other"
    new_citations: list[LLMCitation] = Field(default_factory=list)
    old_citations: list[LLMCitation] = Field(default_factory=list)


class LLMChanges(BaseModel):
    changes: list[LLMChange] = Field(default_factory=list)


# -------------------------------------------------------------- wrappers
class ChangeItem(BaseModel):
    addendum: str  # e.g. "Addendum No. 1"
    kind: str  # one of CHANGE_KINDS
    summary: str  # one plain-English line
    affects: str = "other"  # a brief field name, "bid_items" or "other"
    new_citations: list[Citation] = Field(default_factory=list)  # the addendum passage
    old_citations: list[Citation] = Field(default_factory=list)  # the original passage, when found
    source: Literal["model", "bid_form"] = "model"


class AddendumInfo(BaseModel):
    label: str
    file: str
    pages: list[int] = Field(default_factory=list)
    unreadable_pages: list[int] = Field(default_factory=list)


class ChangesResult(BaseModel):
    addenda: list[AddendumInfo] = Field(default_factory=list)
    changes: list[ChangeItem] = Field(default_factory=list)
    dropped_unverified: int = 0
    note: str | None = None

class PageInfo(BaseModel):
    file: str
    page: int
    section: str | None = None
    is_ocr: bool = False
    char_count: int = 0
    problem: str | None = None  # set when the page needs a manual look


class ReadFirstItem(BaseModel):
    reason: str
    citations: list[Citation] = Field(default_factory=list)


class Brief(BaseModel):
    fields: list[BriefField] = Field(default_factory=list)
    read_first: list[ReadFirstItem] = Field(default_factory=list)
    note: str | None = None  # e.g. "No model key set"

    def get(self, name: str) -> BriefField | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None


class BidItemsResult(BaseModel):
    items: list[BidItem] = Field(default_factory=list)
    bid_form_pages: list[tuple[str, int]] = Field(default_factory=list)
    numbering_gaps: list[str] = Field(default_factory=list)
    duplicates: list[str] = Field(default_factory=list)
    method: str = ""  # "table", "text", "model" or a mix


class RisksResult(BaseModel):
    flags: list[RiskFlag] = Field(default_factory=list)
    dropped_unverified: int = 0
    note: str | None = None


class AskResult(BaseModel):
    question: str
    answer: Answer
    status: Literal["verified", "unverified", "not_found", "no_model"]
    checked_pages: list[tuple[str, int]] = Field(default_factory=list)
    raw_citations: int = 0  # citations the model returned before the check


class PackageResult(BaseModel):
    package_id: str
    title: str
    files: list[str]
    page_count: int
    pages: list[PageInfo] = Field(default_factory=list)
    brief: Brief = Field(default_factory=Brief)
    bid_items: BidItemsResult = Field(default_factory=BidItemsResult)
    risks: RisksResult = Field(default_factory=RisksResult)
    timings: dict[str, float] = Field(default_factory=dict)
    citation_stats: dict[str, int] = Field(default_factory=dict)
    model: str = ""
    created_at: str = ""
    synthetic: bool = False
    example_answers: list[AskResult] = Field(default_factory=list)  # pre-computed for the demo buttons
    changes: ChangesResult = Field(default_factory=ChangesResult)

    @property
    def manual_look_pages(self) -> list[PageInfo]:
        return [p for p in self.pages if p.problem]
