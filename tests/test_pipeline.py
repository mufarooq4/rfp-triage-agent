from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import main
from app.agent import TriageAgent
from app.pdf_reader import PDFError, read_pdf
from app.scoring import score
from app.verify import Verifier
from tests.fixtures import (
    JANITORIAL_PAGES, SOFTWARE_PAGES, FakeClient, janitorial_extraction, make_pdf, make_struck_pdf,
    software_extraction,
)

TODAY = date(2026, 11, 24)


# ------------------------------------------------------------------ PDF reading
def test_reads_every_page_with_markers():
    pdf = read_pdf(make_pdf(SOFTWARE_PAGES))
    assert pdf.page_count == 3
    tagged = pdf.tagged_text()
    assert "=== PDF PAGE 1 ===" in tagged and "=== PDF PAGE 3 ===" in tagged
    assert "SOC 2 Type II" in pdf.pages[2]


def test_rejects_non_pdf():
    with pytest.raises(PDFError):
        read_pdf(b"hello world")


def test_rejects_scanned_looking_pdf():
    with pytest.raises(PDFError, match="scanned"):
        read_pdf(make_pdf([[""], [""]]))


def test_detects_struck_out_dates():
    pdf = read_pdf(make_struck_pdf())
    assert pdf.struck == [(1, "January 8, 2027")]
    assert "[STRUCK OUT: January 8, 2027] January 15, 2027" in pdf.pages[0]
    # a quote copied without the marker still verifies
    v = Verifier(pdf.pages)
    assert v.check_quote("Proposals due January 8, 2027 January 15, 2027", 1).status == "verified"


# ------------------------------------------------------------------ verification
def test_quote_statuses():
    v = Verifier(read_pdf(make_pdf(SOFTWARE_PAGES)).pages)
    assert v.check_quote("Estimated value: $450,000 over 18 months.", 2).status == "verified"
    moved = v.check_quote("Written questions must be received by December 4, 2026.", 1)
    assert moved.status == "page_corrected" and moved.found_on_page == 3
    assert v.check_quote("All staff must hold an active Secret clearance.", 3).status == "unverified"
    assert v.check_quote("Estimated  value: $450,000  over 18 month", 2).status in ("verified", "approximate")
    assert v.check_quote(None, 1).status == "no_quote"
    # pieces joined with "..." are checked one by one
    assert v.check_quote("Contract type: firm fixed price. ... Estimated value: $450,000", 2).status == "verified"
    assert v.check_quote("Contract type: firm fixed price. ... a made-up clause about drones", 2).status == "unverified"


def test_weekday_and_date_mismatch_flags():
    v = Verifier(["Proposals due Thursday, December 18, 2026."])
    issues = v.check_date("Thursday, December 18, 2026", "2026-12-18", 1)["issues"]
    assert any("Friday" in i for i in issues)  # 18 Dec 2026 is a Friday
    issues = v.check_date("Thursday, December 18, 2026", "2026-12-17", 1)["issues"]
    assert any("reads as 2026-12-18" in i for i in issues)
    assert v.check_date("30 days after award", None, 1)["issues"] == [
        "Date text not found in the document -- check manually."
    ]


# ------------------------------------------------------------------ scoring
def test_software_rfp_scores_as_bid():
    r = score(software_extraction())
    assert r["decision"] == "BID"
    assert 6.0 <= r["fit_score"] <= 10
    assert len(r["factors"]) == 8                                   # missing factor filled in
    assert any(f.factor == "location_onsite" and f.rating == "not_stated" for f in r["factors"])


def test_janitorial_is_fast_no_bid():
    r = score(janitorial_extraction())
    assert r["decision"] == "NO_BID" and r["fast_no_bid"] is True
    assert r["fit_score"] <= 2.0
    assert r["confidence"] == "high"


# ------------------------------------------------------------------ full pipeline
def test_end_to_end_report_shape():
    agent = TriageAgent(client=FakeClient(software_extraction()))
    rep = agent.triage(make_pdf(SOFTWARE_PAGES), "software.pdf", today=TODAY)
    for key in ("decision", "fit_score", "deadlines", "requirements", "reasons"):
        assert key in rep                                           # shape from the brief
    assert rep["decision"] == "BID" and len(rep["reasons"]) == 3
    # dates are real calendar dates, sorted, with countdown
    assert [d["date"] for d in rep["key_dates"]] == ["2026-12-04", "2026-12-18", "2027-02-01"]
    assert rep["key_dates"][1]["days_until"] == 24
    q = next(d for d in rep["key_dates"] if d["category"] == "questions_due")
    assert q["verification"] == "page_corrected" and q["page"] == 3
    # hallucinated requirement is caught
    fake = next(x for x in rep["requirements"] if "Secret" in x["text"])
    assert fake["verification"] == "unverified"
    assert rep["evidence"]["unverified"] >= 1
    assert any("could not be found" in w for w in rep["warnings"])
    # "Not stated" preserved
    assert rep["facts"]["submission_method"]["value"] == "Not stated"


def test_reasons_always_support_the_decision():
    # BID -> reasons come from the "to bid" list
    agent = TriageAgent(client=FakeClient(software_extraction()))
    rep = agent.triage(make_pdf(SOFTWARE_PAGES), "sw.pdf", today=TODAY)
    assert rep["reasons"][0].startswith("Workflow automation")
    # NO_BID -> hard gate first, then the "not to bid" list; never a pro-bid reason
    agent = TriageAgent(client=FakeClient(janitorial_extraction()))
    rep = agent.triage(make_pdf(JANITORIAL_PAGES), "jan.pdf", today=TODAY)
    assert rep["decision"] == "NO_BID" and len(rep["reasons"]) == 3
    assert "Fast no-bid" in rep["reasons"][0]
    assert "Clear schedule (p. 1)" not in rep["reasons"]
    assert rep["deal_breakers"] and rep["verdict_line"].startswith("NO-BID")


def test_cache_hit_on_same_file():
    client = FakeClient(software_extraction())
    agent = TriageAgent(client=client)
    data = make_pdf(SOFTWARE_PAGES)
    agent.triage(data, today=TODAY)
    rep = agent.triage(data, today=TODAY)
    assert rep["timing"]["cache_hit"] is True
    assert len(client.prompts) == 1                                # one model call, then cached


# ------------------------------------------------------------------ HTTP API
def test_api_endpoints(monkeypatch):
    monkeypatch.setattr(main, "agent", TriageAgent(client=FakeClient(software_extraction())))
    c = TestClient(main.app)
    assert c.get("/api/health").json()["status"] == "ok"
    assert "builds" in c.get("/api/profile").json()
    assert c.get("/").status_code == 200
    r = c.post("/api/triage?include_pages=true", files={"file": ("rfp.pdf", make_pdf(SOFTWARE_PAGES), "application/pdf")})
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "BID" and len(body["pages"]) == 3
    bad = c.post("/api/triage", files={"file": ("x.pdf", b"not a pdf", "application/pdf")})
    assert bad.status_code == 422


def test_safety_net_lists_uncovered_must_statements():
    from app.coverage import coverage
    pages = read_pdf(make_pdf(SOFTWARE_PAGES)).pages
    # nothing extracted -> both "shall"/"must" requirement sentences on page 3 are listed
    c = coverage(pages, quotes=[], texts=[])
    texts = [u["text"] for u in c["unlisted"]]
    assert any("SOC 2 Type II" in t for t in texts)
    assert any("three references" in t for t in texts)
    # once the SOC 2 requirement is extracted, it is no longer listed
    c2 = coverage(pages, quotes=["The vendor shall hold SOC 2 Type II attestation."], texts=[])
    assert not any("SOC 2" in u["text"] for u in c2["unlisted"])
    assert c2["covered"] == c["covered"] + 1


def test_report_includes_safety_net():
    agent = TriageAgent(client=FakeClient(software_extraction()))
    rep = agent.triage(make_pdf(SOFTWARE_PAGES), "sw.pdf", today=TODAY)
    assert "other_must_statements" in rep and rep["coverage"]["mandatory_statements"] >= 2
