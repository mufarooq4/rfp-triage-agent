"""Deterministic fit score and bid/no-bid decision.

Design choice: the model *reads* the RFP and rates each factor; this module
*decides*. Weights and thresholds are fixed numbers in code, so the same ratings
always give the same score, the score is explainable line by line, and the bids
team can tune it without touching a prompt.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .schemas import Extraction, FactorName, FitFactor, Rating, ScoredFactor, WorkCategory

# Weights sum to 10, so a perfect RFP scores 10.0.
WEIGHTS: Dict[FactorName, float] = {
    FactorName.service_match: 3.0,
    FactorName.technical_alignment: 1.5,
    FactorName.eligibility_set_aside: 1.5,
    FactorName.certifications_clearances: 1.0,
    FactorName.team_size_staffing: 1.0,
    FactorName.location_onsite: 0.75,
    FactorName.past_performance: 0.75,
    FactorName.value_and_timeline: 0.5,
}

LABELS: Dict[FactorName, str] = {
    FactorName.service_match: "Matches what we build",
    FactorName.technical_alignment: "Technical stack / AI alignment",
    FactorName.eligibility_set_aside: "Eligibility & set-asides",
    FactorName.certifications_clearances: "Certifications & clearances",
    FactorName.team_size_staffing: "Team size & staffing load",
    FactorName.location_onsite: "Location & on-site needs",
    FactorName.past_performance: "Past-performance asks",
    FactorName.value_and_timeline: "Contract value & timeline",
}

# Share of a factor's weight earned for each rating. "Not stated" is neutral:
# missing information should not push the score either way.
MULTIPLIER: Dict[Rating, float] = {
    Rating.strong_positive: 1.0,
    Rating.positive: 0.75,
    Rating.neutral: 0.5,
    Rating.not_stated: 0.5,
    Rating.negative: 0.15,
    Rating.deal_breaker: 0.0,
}

# Work we clearly do not do -> fast no-bid regardless of other factors.
OUT_OF_DOMAIN = {
    WorkCategory.construction,
    WorkCategory.facilities_janitorial,
    WorkCategory.goods_supply,
    WorkCategory.professional_non_it,
}
# Adjacent work: possible, but never a strong fit on category alone.
ADJACENT = {
    WorkCategory.it_services_staffing,
    WorkCategory.it_hardware_infrastructure,
    WorkCategory.consulting_advisory,
    WorkCategory.data_analytics,
    WorkCategory.other,
}

BID_THRESHOLD = 6.0
BORDERLINE_BAND = 1.0      # scores within +/-1.0 of the threshold are flagged borderline
OUT_OF_DOMAIN_CAP = 2.0
DEAL_BREAKER_CAP = 4.0


def _complete_factors(factors: List[FitFactor]) -> Dict[FactorName, FitFactor]:
    """Keep the first rating per factor and fill any the model skipped."""
    by_name: Dict[FactorName, FitFactor] = {}
    for f in factors:
        by_name.setdefault(f.factor, f)
    for name in WEIGHTS:
        if name not in by_name:
            by_name[name] = FitFactor(
                factor=name, rating=Rating.not_stated, reason="Not stated in the document."
            )
    return by_name


def score(extraction: Extraction) -> dict:
    factors = _complete_factors(extraction.fit_factors)
    scored: List[ScoredFactor] = []
    raw = 0.0
    for name, weight in WEIGHTS.items():
        f = factors[name]
        pts = round(weight * MULTIPLIER[f.rating], 2)
        raw += pts
        scored.append(ScoredFactor(
            factor=name.value, label=LABELS[name], weight=weight, rating=f.rating.value,
            points=pts, reason=f.reason, page=f.page, quote=f.quote,
        ))

    gates: List[str] = []
    final = raw
    fast_no_bid = extraction.work_category in OUT_OF_DOMAIN
    if fast_no_bid:
        gates.append(
            f"Fast no-bid: this is {extraction.work_category.value.replace('_', ' ')} work, "
            "which idiaz.io does not do."
        )
        final = min(final, OUT_OF_DOMAIN_CAP)

    breakers = [s for s in scored if s.rating == Rating.deal_breaker.value]
    for b in breakers:
        gates.append(f"Deal-breaker -- {b.label}: {b.reason}")
    if breakers:
        final = min(final, DEAL_BREAKER_CAP)

    if extraction.work_category in ADJACENT and final > 7.0:
        gates.append("Capped at 7.0: the work is adjacent to, not squarely within, what we build.")
        final = 7.0

    final = round(final, 1)
    decision = "BID" if (final >= BID_THRESHOLD and not breakers and not fast_no_bid) else "NO_BID"
    borderline = (not fast_no_bid) and abs(final - BID_THRESHOLD) <= BORDERLINE_BAND

    return {
        "fit_score": final,
        "raw_score": round(raw, 2),
        "decision": decision,
        "fast_no_bid": fast_no_bid,
        "borderline": borderline,
        "confidence": _confidence(final, borderline, fast_no_bid, scored),
        "gates": gates,
        "factors": scored,
        "method": {
            "weights": {k.value: v for k, v in WEIGHTS.items()},
            "multipliers": {k.value: v for k, v in MULTIPLIER.items()},
            "bid_threshold": BID_THRESHOLD,
        },
    }


def _confidence(final: float, borderline: bool, fast: bool, scored: List[ScoredFactor]) -> str:
    if fast:
        return "high"
    unknown = sum(1 for s in scored if s.rating == Rating.not_stated.value)
    if borderline or unknown >= 4:
        return "low"
    if abs(final - BID_THRESHOLD) >= 2.5 and unknown <= 1:
        return "high"
    return "medium"


def fallback_reasons(result: dict) -> List[str]:
    """Reasons built from the factor breakdown; used to pad when the model lists too few."""
    factors: List[ScoredFactor] = result["factors"]
    if result["decision"] == "BID":
        top = sorted(factors, key=lambda s: s.points / s.weight, reverse=True)
    else:
        top = sorted(factors, key=lambda s: (s.points / s.weight, -s.weight))
    reasons = list(result["gates"][:3])
    for s in top:
        if len(reasons) >= 3:
            break
        reasons.append(f"{s.label}: {s.reason}")
    return reasons[:3]


def pick_reasons(extraction: Extraction, result: dict) -> dict:
    """Choose the three reasons that SUPPORT the decision code already made.

    The model lists the strongest reasons on both sides; it never decides. Code picks
    the side that matches the decision, so the reasons can never argue against the call.
    """
    bid = result["decision"] == "BID"
    pool: List[str] = []
    if not bid:
        # one hard blocker first, then the document-specific reasons (avoids three
        # versions of "this is janitorial work"), then any remaining gates
        pool += result["gates"][:1]
        pool += extraction.reasons_not_to_bid
        pool += result["gates"][1:]
    else:
        pool += extraction.reasons_to_bid
    pool += fallback_reasons(result)                # pad from the score if the model gave too few
    reasons: List[str] = []
    for r in pool:
        r = (r or "").strip()
        if r and r not in reasons:
            reasons.append(r)
        if len(reasons) == 3:
            break

    breakers = [f"{s.label}: {s.reason}" for s in result["factors"] if s.rating == Rating.deal_breaker.value]
    if result["fast_no_bid"] and not breakers:
        breakers.append(result["gates"][0])
    label = "BID" if bid else "NO-BID"
    lead = reasons[0] if reasons else ""
    return {
        "reasons": reasons,
        "deal_breakers": breakers,
        "what_would_change_our_mind": list(extraction.what_would_change_our_mind or []),
        "verdict_line": f"{label} at {result['fit_score']:.1f}/10. {lead}".strip(),
    }


def rating_label(r: Optional[str]) -> str:
    return (r or "").replace("_", " ")
