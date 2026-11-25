"""Synthetic RFP PDFs and a fake model client for offline tests."""

from __future__ import annotations

import io
from typing import List

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.schemas import (
    DateCategory, Extraction, Fact, FactorName, FitFactor, FitStatus, KeyDate, Rating,
    Requirement, RequirementCategory, WorkCategory,
)

SOFTWARE_PAGES: List[List[str]] = [
    [
        "REQUEST FOR PROPOSALS",
        "RFP No. CITY-IT-2026-014",
        "City of Example Springs, Department of Permits",
        "Permit Workflow Automation Platform",
        "Proposals are due no later than Friday, December 18, 2026 at 3:00 PM Eastern Time.",
    ],
    [
        "SECTION 2 - SCOPE OF WORK",
        "The City seeks a vendor to design and deliver a web-based permit workflow automation",
        "platform with document intake, AI-assisted plan review triage and a public status portal.",
        "Contract type: firm fixed price. Estimated value: $450,000 over 18 months.",
    ],
    [
        "SECTION 3 - SCHEDULE",
        "Written questions must be received by December 4, 2026.",
        "Anticipated contract start: February 1, 2027.",
        "SECTION 4 - REQUIREMENTS",
        "The vendor shall hold SOC 2 Type II attestation.",
        "The vendor must provide three references for similar government projects.",
    ],
]

JANITORIAL_PAGES: List[List[str]] = [
    [
        "INVITATION FOR BIDS",
        "Bid No. LC-2026-77 Janitorial Services for County Buildings",
        "Bids due January 9, 2027 at 2:00 PM local time.",
        "Contractor shall provide 24 custodial staff for nightly cleaning of 12 facilities.",
    ],
]


def make_pdf(pages: List[List[str]]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for lines in pages:
        y = 740
        for line in lines:
            c.drawString(60, y, line)
            y -= 22
        c.showPage()
    c.save()
    return buf.getvalue()


def make_struck_pdf() -> bytes:
    """One page where 'January 8, 2027' is crossed out and replaced, like an addendum."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont("Helvetica", 11)
    c.drawString(60, 700, "Proposals due January 8, 2027 January 15, 2027, 3:00 PM Pacific")
    x0 = 60 + c.stringWidth("Proposals due ", "Helvetica", 11)
    w = c.stringWidth("January 8, 2027", "Helvetica", 11)
    c.rect(x0, 703.5, w, 0.75, stroke=0, fill=1)          # strike line through the middle
    c.line(60, 690, 300, 690)                             # an underline-like rule: must NOT count
    c.drawString(60, 670, "Addendum No. 1 extends the due date to January 15, 2027. " * 2)
    c.showPage()
    c.save()
    return buf.getvalue()


def software_extraction() -> Extraction:
    return Extraction(
        title=Fact(value="Permit Workflow Automation Platform", page=1, quote="Permit Workflow Automation Platform"),
        solicitation_number=Fact(value="CITY-IT-2026-014", page=1, quote="RFP No. CITY-IT-2026-014"),
        agency=Fact(value="City of Example Springs, Department of Permits", page=1,
                    quote="City of Example Springs, Department of Permits"),
        summary="The City wants a web platform that automates permit intake and review.",
        scope_of_work=["Document intake", "AI-assisted plan review triage", "Public status portal"],
        work_category=WorkCategory.automation_workflow,
        work_category_reason="Workflow automation software with AI triage.",
        contract_type=Fact(value="Firm fixed price", page=2, quote="Contract type: firm fixed price."),
        contract_value=Fact(value="$450,000 over 18 months", page=2, quote="Estimated value: $450,000 over 18 months."),
        period_of_performance=Fact(value="18 months", page=2, quote="$450,000 over 18 months"),
        place_of_performance=Fact(value="Not stated"),
        submission_method=Fact(value="Not stated"),
        point_of_contact=Fact(value="Not stated"),
        evaluation_method=Fact(value="Not stated"),
        key_dates=[
            KeyDate(what="Proposals due", category=DateCategory.proposal_due,
                    as_written="Friday, December 18, 2026 at 3:00 PM Eastern Time",
                    date_iso="2026-12-18", time="15:00", timezone="Eastern Time", page=1,
                    quote="Proposals are due no later than Friday, December 18, 2026"),
            # deliberately wrong page: verifier should correct it to page 3
            KeyDate(what="Questions due", category=DateCategory.questions_due,
                    as_written="December 4, 2026", date_iso="2026-12-04", page=1,
                    quote="Written questions must be received by December 4, 2026."),
            KeyDate(what="Contract start", category=DateCategory.contract_start,
                    as_written="February 1, 2027", date_iso="2027-02-01", page=3,
                    quote="Anticipated contract start: February 1, 2027."),
        ],
        requirements=[
            Requirement(category=RequirementCategory.certification, text="SOC 2 Type II attestation",
                        mandatory=True, idiaz_status=FitStatus.unknown, page=3,
                        quote="The vendor shall hold SOC 2 Type II attestation."),
            Requirement(category=RequirementCategory.past_performance, text="Three government references",
                        mandatory=True, idiaz_status=FitStatus.unknown, page=3,
                        quote="The vendor must provide three references for similar government projects."),
            # hallucinated: not in the PDF -> must be flagged unverified
            Requirement(category=RequirementCategory.security_clearance, text="Secret clearance",
                        mandatory=True, idiaz_status=FitStatus.gap, page=3,
                        quote="All staff must hold an active Secret clearance."),
        ],
        fit_factors=[
            FitFactor(factor=FactorName.service_match, rating=Rating.strong_positive,
                      reason="Automation platform is core work.", page=2,
                      quote="web-based permit workflow automation"),
            FitFactor(factor=FactorName.technical_alignment, rating=Rating.strong_positive,
                      reason="AI-assisted triage.", page=2, quote="AI-assisted plan review triage"),
            FitFactor(factor=FactorName.eligibility_set_aside, rating=Rating.positive, reason="No set-aside."),
            FitFactor(factor=FactorName.certifications_clearances, rating=Rating.neutral, reason="SOC 2 status unknown."),
            FitFactor(factor=FactorName.team_size_staffing, rating=Rating.positive, reason="Small delivery team suffices."),
            FitFactor(factor=FactorName.past_performance, rating=Rating.neutral, reason="References needed."),
            FitFactor(factor=FactorName.value_and_timeline, rating=Rating.positive, reason="$450k over 18 months."),
            # location_onsite omitted on purpose -> filled as not_stated
        ],
        ambiguities=[],
        open_questions=["Is SOC 2 Type I acceptable during the first year?"],
        reasons_to_bid=["Workflow automation with AI triage is core idiaz.io work (p. 2)",
                        "$450,000 over 18 months suits a small senior team (p. 2)",
                        "No set-aside restricts eligibility (p. 1)"],
        reasons_not_to_bid=["SOC 2 Type II is mandatory and our status is unknown (p. 3)"],
        what_would_change_our_mind=["A mandatory clearance or set-aside we cannot meet."],
    )


def janitorial_extraction() -> Extraction:
    return Extraction(
        title=Fact(value="Janitorial Services for County Buildings", page=1,
                   quote="Janitorial Services for County Buildings"),
        solicitation_number=Fact(value="LC-2026-77", page=1, quote="Bid No. LC-2026-77"),
        agency=Fact(value="Not stated"),
        summary="Nightly cleaning of 12 county facilities.",
        scope_of_work=["Nightly custodial services"],
        work_category=WorkCategory.facilities_janitorial,
        work_category_reason="Custodial services.",
        contract_type=Fact(value="Not stated"), contract_value=Fact(value="Not stated"),
        period_of_performance=Fact(value="Not stated"), place_of_performance=Fact(value="Not stated"),
        submission_method=Fact(value="Not stated"), point_of_contact=Fact(value="Not stated"),
        evaluation_method=Fact(value="Not stated"),
        key_dates=[
            # weekday deliberately wrong in as_written check is separate; here clean
            KeyDate(what="Bids due", category=DateCategory.proposal_due,
                    as_written="January 9, 2027 at 2:00 PM local time", date_iso="2027-01-09",
                    time="14:00", timezone="local time", page=1, quote="Bids due January 9, 2027"),
        ],
        requirements=[
            Requirement(category=RequirementCategory.staffing, text="24 custodial staff",
                        mandatory=True, idiaz_status=FitStatus.gap, page=1,
                        quote="Contractor shall provide 24 custodial staff"),
        ],
        fit_factors=[
            FitFactor(factor=FactorName.service_match, rating=Rating.deal_breaker, reason="Janitorial work."),
            FitFactor(factor=FactorName.team_size_staffing, rating=Rating.deal_breaker, reason="24 staff nightly."),
        ],
        ambiguities=[],
        open_questions=[],
        reasons_to_bid=["Clear schedule (p. 1)"],
        reasons_not_to_bid=["Requires 24 custodial staff every night (p. 1)"],
        what_would_change_our_mind=[],
    )


class FakeClient:
    """Stands in for GeminiClient: returns a canned extraction, records prompts."""

    configured = True
    models = ["fake-model"]
    last_model = "fake-model"

    def __init__(self, extraction: Extraction):
        self.extraction = extraction
        self.prompts: List[str] = []

    def generate(self, prompt, schema):
        self.prompts.append(prompt)
        return self.extraction, "fake-model"
