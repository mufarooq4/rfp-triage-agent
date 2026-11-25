"""Evidence verification: check every quote and date against the PDF text.

The model is good at reading but can still misquote, cite the wrong page or
normalise a date incorrectly. None of these checks use a model -- they are plain
string and calendar logic, so they are fast, repeatable and explainable.

Statuses
  verified        quote found word-for-word on the cited page
  page_corrected  quote found word-for-word, but on a different page (page fixed)
  approximate     near-exact match (>= 90 % similarity) -- usually line-break noise
  unverified      not found -- treat the claim with caution
  no_quote        the model gave no quote (e.g. a "Not stated" field)
"""

from __future__ import annotations

import calendar
import re
from datetime import date
from typing import List, Optional, Tuple

from dateutil import parser as dateparser
from rapidfuzz import fuzz

from .schemas import Verification

_QUOTES = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "−": "-", " ": " ", "•": " ",
})


def normalise(text: str) -> str:
    # quotes match whether or not the model copied our [STRUCK OUT: ...] marker
    text = re.sub(r"\[STRUCK OUT: (.*?)\]", r"\1", text or "")
    text = text.translate(_QUOTES).lower()
    text = re.sub(r"-\s*\n\s*", "", text)          # re-join hyphenated line breaks
    text = re.sub(r"[^\w$%./:@'-]+", " ", text)      # drop layout punctuation
    return re.sub(r"\s+", " ", text).strip()


class Verifier:
    def __init__(self, pages: List[str]):
        self.raw_pages = pages
        self.pages = [normalise(p) for p in pages]

    # ---------------------------------------------------------------- quotes
    def check_quote(self, quote: Optional[str], page: Optional[int]) -> Verification:
        # A quote stitched from pieces with "..." is checked piece by piece:
        # every piece must be in the PDF for the quote to count as verified.
        pieces = [p for p in re.split(r"\.\.\.|\u2026", quote or "") if len(normalise(p)) >= 4]
        if len(pieces) > 1:
            results = [self._check_one(p, page) for p in pieces]
            if any(r.status == "unverified" for r in results):
                worst = min(results, key=lambda r: r.score)
                return Verification(status="unverified", score=worst.score, found_on_page=None)
            status = "verified" if all(r.status == "verified" for r in results) else "approximate"
            return Verification(status=status, score=min(r.score for r in results),
                                found_on_page=results[0].found_on_page)
        return self._check_one(quote, page)

    def _check_one(self, quote: Optional[str], page: Optional[int]) -> Verification:
        q = normalise(quote or "")
        if len(q) < 4:
            return Verification(status="no_quote")

        if page and 1 <= page <= len(self.pages) and q in self.pages[page - 1]:
            return Verification(status="verified", score=100.0, found_on_page=page)

        for i, text in enumerate(self.pages, start=1):
            if q in text:
                return Verification(status="page_corrected", score=100.0, found_on_page=i)

        best_score, best_page = self._best_fuzzy(q, page)
        if best_score >= 90:
            return Verification(status="approximate", score=round(best_score, 1), found_on_page=best_page)
        return Verification(status="unverified", score=round(best_score, 1), found_on_page=None)

    def _best_fuzzy(self, q: str, page: Optional[int]) -> Tuple[float, Optional[int]]:
        order = list(range(1, len(self.pages) + 1))
        if page and 1 <= page <= len(self.pages):          # look at the cited page first
            order.remove(page)
            order.insert(0, page)
        best, best_page = 0.0, None
        for i in order:
            s = fuzz.partial_ratio(q, self.pages[i - 1])
            if s > best:
                best, best_page = s, i
            if best >= 97:
                break
        return best, best_page

    # ----------------------------------------------------------------- dates
    def check_date(self, as_written: str, date_iso: Optional[str], page: Optional[int]) -> dict:
        """Return issues found with one extracted date (empty list = clean)."""
        issues: List[str] = []
        text_check = self.check_quote(as_written, page)
        if text_check.status == "unverified":
            issues.append("Date text not found in the document -- check manually.")

        parsed_iso: Optional[date] = None
        if date_iso:
            try:
                parsed_iso = date.fromisoformat(date_iso)
            except ValueError:
                issues.append(f"'{date_iso}' is not a valid calendar date.")

        if parsed_iso:
            # 1) does the written text parse to the same calendar date?
            written_date = _parse_written_date(as_written, default_year=parsed_iso.year)
            if written_date and written_date != parsed_iso:
                issues.append(
                    f"Document text '{as_written}' reads as {written_date.isoformat()}, "
                    f"not {parsed_iso.isoformat()}."
                )
            # 2) weekday sanity check: "Friday, March 14" must be a Friday
            weekday = _weekday_in(as_written)
            if weekday is not None and weekday != parsed_iso.weekday():
                issues.append(
                    f"Document says {calendar.day_name[weekday]}, but {parsed_iso.isoformat()} "
                    f"is a {calendar.day_name[parsed_iso.weekday()]} -- the agency may have a typo."
                )
        return {"text_verification": text_check, "issues": issues}


_WEEKDAYS = {name.lower(): i for i, name in enumerate(calendar.day_name)}
_WEEKDAYS.update({name.lower(): i for i, name in enumerate(calendar.day_abbr)})


def _weekday_in(text: str) -> Optional[int]:
    for token in re.findall(r"[A-Za-z]+", text or ""):
        t = token.lower().rstrip(".")
        if t in _WEEKDAYS and len(t) >= 3:
            return _WEEKDAYS[t]
    return None


def _parse_written_date(text: str, default_year: int) -> Optional[date]:
    """Best-effort parse of a printed date; None if it carries no explicit date."""
    if not text or not re.search(r"\d", text):
        return None
    has_month_name = re.search(
        r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b", text, re.I
    )
    has_numeric_date = re.search(r"\b\d{1,4}[/-]\d{1,2}[/-]\d{1,4}\b", text)
    if not (has_month_name or has_numeric_date):
        return None  # e.g. "30 days after award", "2:00 PM"
    cleaned = re.sub(r"\b(no later than|by|on|at|before|until|noon|local time|[A-Z]{2,4}T)\b", " ", text, flags=re.I)
    cleaned = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", cleaned)
    try:
        dt = dateparser.parse(cleaned, fuzzy=True, default=_default_dt(default_year))
        return dt.date()
    except (ValueError, OverflowError):
        return None


def _default_dt(year: int):
    from datetime import datetime
    return datetime(year, 1, 1)
