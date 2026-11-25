"""Data shapes for the RFP Triage Agent.

Two layers:
  * ``Extraction`` -- what the language model is asked to return
    (one call). These are passed to Gemini as a response schema, so the model cannot return
    free-form text.
  * ``TriageReport`` -- the final report the API returns. It is assembled in code
    from the extraction, the deterministic score and the verification results.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

NOT_STATED = "Not stated"


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class WorkCategory(str, Enum):
    software_development = "software_development"
    ai_ml = "ai_ml"
    automation_workflow = "automation_workflow"
    compliance_tooling = "compliance_tooling"
    web_mobile = "web_mobile"
    data_analytics = "data_analytics"
    it_services_staffing = "it_services_staffing"
    it_hardware_infrastructure = "it_hardware_infrastructure"
    consulting_advisory = "consulting_advisory"
    construction = "construction"
    facilities_janitorial = "facilities_janitorial"
    goods_supply = "goods_supply"
    professional_non_it = "professional_non_it"
    other = "other"


class DateCategory(str, Enum):
    questions_due = "questions_due"
    proposal_due = "proposal_due"
    site_visit = "site_visit"
    pre_proposal_conference = "pre_proposal_conference"
    intent_to_bid = "intent_to_bid"
    award = "award"
    contract_start = "contract_start"
    contract_end = "contract_end"
    other = "other"


class RequirementCategory(str, Enum):
    certification = "certification"
    set_aside = "set_aside"
    security_clearance = "security_clearance"
    past_performance = "past_performance"
    staffing = "staffing"
    technical_skill = "technical_skill"
    licensing_registration = "licensing_registration"
    insurance_bonding = "insurance_bonding"
    location_onsite = "location_onsite"
    submission_format = "submission_format"
    other = "other"


class FitStatus(str, Enum):
    meets = "meets"
    gap = "gap"
    unknown = "unknown"
    not_applicable = "not_applicable"


class FactorName(str, Enum):
    service_match = "service_match"
    technical_alignment = "technical_alignment"
    eligibility_set_aside = "eligibility_set_aside"
    certifications_clearances = "certifications_clearances"
    team_size_staffing = "team_size_staffing"
    location_onsite = "location_onsite"
    past_performance = "past_performance"
    value_and_timeline = "value_and_timeline"


class Rating(str, Enum):
    strong_positive = "strong_positive"
    positive = "positive"
    neutral = "neutral"
    not_stated = "not_stated"
    negative = "negative"
    deal_breaker = "deal_breaker"


# --------------------------------------------------------------------------- #
# What the model returns (call 1: extraction)
# --------------------------------------------------------------------------- #
class Fact(BaseModel):
    value: str = Field(description='The fact in plain words, or "Not stated".')
    page: Optional[int] = Field(None, description="PDF page number where it appears.")
    quote: Optional[str] = Field(
        None, description="Short verbatim quote (max ~30 words) copied exactly from that page."
    )


class KeyDate(BaseModel):
    what: str = Field(description="What happens on this date, e.g. 'Proposals due'.")
    category: DateCategory
    as_written: str = Field(description="The date/time exactly as printed in the document.")
    date_iso: Optional[str] = Field(
        None, description="YYYY-MM-DD if the document gives a calendar date; null if relative or missing."
    )
    time: Optional[str] = Field(None, description="24h HH:MM if a time is given.")
    timezone: Optional[str] = Field(None, description="Time zone exactly as stated, if any.")
    page: Optional[int] = None
    quote: Optional[str] = None
    ambiguous: bool = Field(False, description="True if the date is unclear, relative or contradicted.")
    ambiguity_note: Optional[str] = None


class Requirement(BaseModel):
    category: RequirementCategory
    text: str = Field(description="The requirement, stated precisely.")
    mandatory: bool = Field(description="True if the document makes it a must (shall/must/required).")
    idiaz_status: FitStatus = Field(description="Does idiaz.io meet it, judged only from the profile?")
    status_note: Optional[str] = Field(None, description="One line explaining the status.")
    page: Optional[int] = None
    quote: Optional[str] = None


class FitFactor(BaseModel):
    factor: FactorName
    rating: Rating
    reason: str = Field(description="One line: why this rating, grounded in the document.")
    page: Optional[int] = None
    quote: Optional[str] = None


class Ambiguity(BaseModel):
    issue: str = Field(description="What is unclear or contradictory.")
    readings: List[str] = Field(default_factory=list, description="The possible interpretations.")
    page: Optional[int] = None
    quote: Optional[str] = None


class Extraction(BaseModel):
    title: Fact
    solicitation_number: Fact
    agency: Fact
    summary: str = Field(description="What the client wants, 3-5 plain-English sentences.")
    scope_of_work: List[str] = Field(description="3-8 bullets describing the work.")
    work_category: WorkCategory
    work_category_reason: str
    contract_type: Fact
    contract_value: Fact
    period_of_performance: Fact
    place_of_performance: Fact
    submission_method: Fact
    point_of_contact: Fact
    evaluation_method: Fact
    key_dates: List[KeyDate]
    requirements: List[Requirement]
    fit_factors: List[FitFactor]
    ambiguities: List[Ambiguity]
    open_questions: List[str] = Field(description="Questions idiaz.io should ask the agency.")
    reasons_to_bid: List[str] = Field(description="3-5 strongest, strongest first, each with (p. N).")
    reasons_not_to_bid: List[str] = Field(description="3-5 strongest, strongest first, each with (p. N).")
    what_would_change_our_mind: List[str] = Field(description="1-3 facts that would flip the call.")


# --------------------------------------------------------------------------- #
# Final report (assembled in code)
# --------------------------------------------------------------------------- #
class Verification(BaseModel):
    status: str  # verified | page_corrected | approximate | unverified | no_quote
    score: float = 0.0
    found_on_page: Optional[int] = None


class ScoredFactor(BaseModel):
    factor: str
    label: str
    weight: float
    rating: str
    points: float
    reason: str
    page: Optional[int] = None
    quote: Optional[str] = None
    verification: Optional[Verification] = None
