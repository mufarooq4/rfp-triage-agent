"""The triage pipeline.

    PDF ──read──▶ page-tagged text ──Gemini──▶ Extraction (facts + quotes + ratings)
        ──verify──▶ every quote/date checked against the PDF (no model)
        ──score───▶ deterministic 0-10 fit score + gates + BID/NO_BID (no model)
        ──pick────▶ the 3 model-listed reasons that support the decision (no model)
        ──────────▶ report JSON

ONE model call. The model reads; the code checks and decides.
"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from . import llm, scoring
from .pdf_reader import ParsedPDF, read_pdf
from .schemas import NOT_STATED, Extraction
from .coverage import coverage
from .verify import Verifier, normalise

FACT_FIELDS = [
    "title", "solicitation_number", "agency", "contract_type", "contract_value",
    "period_of_performance", "place_of_performance", "submission_method",
    "point_of_contact", "evaluation_method",
]


class TriageAgent:
    def __init__(self, client: Optional[llm.GeminiClient] = None, cache_size: int = 32):
        self.client = client or llm.GeminiClient()
        self._cache: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._cache_size = cache_size

    # ------------------------------------------------------------------ API
    def triage(self, data: bytes, filename: str = "rfp.pdf", today: Optional[date] = None) -> Dict[str, Any]:
        digest = hashlib.sha256(data).hexdigest()
        if digest in self._cache:
            cached = dict(self._cache[digest])
            cached["timing"] = {**cached["timing"], "cache_hit": True}
            return cached

        t0 = time.time()
        pdf = read_pdf(data)
        t_read = time.time()

        extraction, model = llm.extract(self.client, pdf.tagged_text())
        t_extract = time.time()

        verifier = Verifier(pdf.pages)
        result = scoring.score(extraction)

        picked = scoring.pick_reasons(extraction, result)
        t_done = time.time()

        report = build_report(
            extraction, picked, result, verifier, pdf,
            filename=filename, digest=digest, model=model, today=today or date.today(),
        )
        report["timing"] = {
            "read_pdf_s": round(t_read - t0, 2),
            "extract_s": round(t_extract - t_read, 2),
            "check_and_decide_s": round(t_done - t_extract, 2),
            "total_s": round(t_done - t0, 2),
            "cache_hit": False,
        }

        self._cache[digest] = report
        self._cache.move_to_end(digest)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return report


# ---------------------------------------------------------------------- helpers
def _ev(verifier: Verifier, page: Optional[int], quote: Optional[str]) -> Dict[str, Any]:
    v = verifier.check_quote(quote, page)
    return {
        "page": v.found_on_page or page,
        "cited_page": page,
        "quote": quote,
        "verification": v.status,
        "match_score": v.score,
    }


def _days_until(d: Optional[str], today: date) -> Optional[int]:
    try:
        return (date.fromisoformat(d) - today).days if d else None
    except ValueError:
        return None


def build_report(
    ex: Extraction, nar: dict, result: dict, verifier: Verifier, pdf: ParsedPDF,
    *, filename: str, digest: str, model: str, today: date,
) -> Dict[str, Any]:
    warnings: List[str] = []

    # ---- facts
    facts = {}
    for name in FACT_FIELDS:
        f = getattr(ex, name)
        value = (f.value or "").strip() or NOT_STATED
        facts[name] = {"value": value, **_ev(verifier, f.page, f.quote)} if value != NOT_STATED else {
            "value": NOT_STATED, "page": None, "cited_page": None, "quote": None,
            "verification": "no_quote", "match_score": 0.0,
        }

    # ---- dates (sorted, with calendar checks)
    dates = []
    for d in ex.key_dates:
        check = verifier.check_date(d.as_written, d.date_iso, d.page)
        quote_ev = _ev(verifier, d.page, d.quote or d.as_written)
        issues = list(check["issues"])
        if d.ambiguity_note:
            issues.insert(0, d.ambiguity_note)
        # safety net: the model must never report crossed-out text as the current date
        for _pg, txt in pdf.struck:
            if normalise(txt) and normalise(txt) in normalise(d.as_written):
                issues.append(f"'{txt}' is crossed out in the PDF -- this date may be superseded.")
        dates.append({
            "what": d.what,
            "category": d.category.value,
            "date": d.date_iso,                 # machine-readable
            "as_written": d.as_written,         # exactly as printed
            "time": d.time,
            "timezone": d.timezone,
            "days_until": _days_until(d.date_iso, today),
            "ambiguous": bool(d.ambiguous or issues),
            "issues": issues,
            **quote_ev,
        })
    dates.sort(key=lambda x: (x["date"] is None, x["date"] or "", x["time"] or ""))

    proposal = [d for d in dates if d["category"] == "proposal_due" and d["date"]]
    if not proposal:
        warnings.append("No proposal due date with a calendar date was found -- check the document.")
    elif all((d["days_until"] or 0) < 0 for d in proposal):
        warnings.append("The proposal deadline appears to have passed.")
    due_dates = {d["date"] for d in proposal}
    if len(due_dates) > 1:
        warnings.append("The document gives more than one proposal due date: " + ", ".join(sorted(due_dates)))

    # ---- requirements
    reqs = []
    for r in ex.requirements:
        reqs.append({
            "category": r.category.value,
            "text": r.text,
            "mandatory": r.mandatory,
            "idiaz_status": r.idiaz_status.value,
            "status_note": r.status_note,
            **_ev(verifier, r.page, r.quote),
        })
    reqs.sort(key=lambda r: (not r["mandatory"], r["idiaz_status"] != "gap", r["category"]))

    # ---- factors (attach verification)
    factors = []
    for f in result["factors"]:
        row = f.model_dump(exclude={"verification"})
        row.update(_ev(verifier, f.page, f.quote) if f.quote else {"verification": "no_quote"})
        factors.append(row)

    ambiguities = [
        {"issue": a.issue, "readings": a.readings, **_ev(verifier, a.page, a.quote)} for a in ex.ambiguities
    ]

    # ---- evidence summary (the trust meter)
    statuses = [x["verification"] for x in [*facts.values(), *dates, *reqs, *factors, *ambiguities]]
    checked = [s for s in statuses if s != "no_quote"]
    ok = [s for s in checked if s in ("verified", "page_corrected", "approximate")]
    unverified_n = sum(1 for s in checked if s == "unverified")
    if pdf.struck:
        warnings.append(
            "Crossed-out text detected and ignored: "
            + "; ".join(f"p. {pg}: \u201c{txt}\u201d" for pg, txt in pdf.struck[:5])
        )
    if unverified_n:
        warnings.append(f"{unverified_n} cited quote(s) could not be found in the PDF -- marked for manual review.")

    # ---- safety net: every "must" sentence in the PDF that the list above does not cover
    cov_quotes = [x.get("quote") or "" for x in [*reqs, *dates, *facts.values()]]
    cov_quotes += [d["as_written"] for d in dates]
    cov_texts = [r["text"] for r in reqs]
    cov = coverage(pdf.pages, cov_quotes, cov_texts)

    deadlines = [
        {"what": d["what"], "date": d["date"] or NOT_STATED, "as_written": d["as_written"],
         "time": d["time"], "timezone": d["timezone"]}
        for d in dates
    ]

    return {
        # ----- the shape from the brief, at the top level -----
        "decision": result["decision"],
        "fit_score": result["fit_score"],
        "deadlines": deadlines,
        "requirements": reqs,
        "reasons": nar["reasons"],
        # ----- everything else -----
        "summary": ex.summary,
        "scope_of_work": ex.scope_of_work,
        "verdict_line": nar["verdict_line"],
        "deal_breakers": nar["deal_breakers"],
        "what_would_change_our_mind": nar["what_would_change_our_mind"],
        "reasons_considered": {"to_bid": ex.reasons_to_bid, "not_to_bid": ex.reasons_not_to_bid},
        "open_questions": ex.open_questions,
        "fast_no_bid": result["fast_no_bid"],
        "borderline": result["borderline"],
        "confidence": result["confidence"],
        "gates": result["gates"],
        "work_category": {"value": ex.work_category.value, "reason": ex.work_category_reason},
        "facts": facts,
        "key_dates": dates,
        "fit_breakdown": {
            "factors": factors,
            "raw_score": result["raw_score"],
            "method": result["method"],
        },
        "ambiguities": ambiguities,
        "other_must_statements": cov["unlisted"],
        "coverage": {"mandatory_statements": cov["mandatory_statements"], "covered_by_requirements": cov["covered"]},
        "evidence": {
            "checked": len(checked),
            "verified": len(ok),
            "unverified": unverified_n,
            "verified_pct": round(100 * len(ok) / len(checked), 1) if checked else None,
        },
        "warnings": warnings,
        "document": {
            "filename": filename,
            "sha256": digest,
            "pages": pdf.page_count,
            "characters": pdf.char_count,
            "struck_out": [{"page": pg, "text": txt} for pg, txt in pdf.struck],
        },
        "model": model,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "today": today.isoformat(),
    }
