"""Safety net for "no requirement is missed": a plain-code scan of the whole PDF.

The model's requirement list is curated and readable, but a model can skip an
item. This module finds every sentence that uses mandatory language (must, shall,
required, mandatory, minimum qualification, or an [M] marker) and reports the ones
that no extracted requirement, date or fact already covers. The bids lead then
sees, in one list, every remaining "must" in the document with its page.

No model is involved, so the scan is fast, repeatable and explainable.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List

from rapidfuzz import fuzz

from .verify import normalise

MANDATORY = re.compile(
    r"\b(must|shall|required|mandatory|minimum qualifications?)\b|\[M\]", re.IGNORECASE
)
SENTENCE_SPLIT = re.compile(r"(?<=[.;!?])\s+(?=[A-Z0-9(\[•])")
MAX_LEN = 320


def mandatory_statements(pages: List[str]) -> List[Dict]:
    """Every sentence with mandatory language, with its PDF page."""
    found, seen = [], set()
    for number, text in enumerate(pages, start=1):
        clean = re.sub(r"\[STRUCK OUT: .*?\]", " ", text)
        clean = re.sub(r"\s+", " ", clean).strip()
        for sentence in SENTENCE_SPLIT.split(clean):
            s = sentence.strip(" •-")
            if len(s) < 25 or len(s.split()) < 6 or not MANDATORY.search(s):
                continue                              # too short: headings, contents-page lines
            if re.match(r"^(shall|must)\s*/", s, re.IGNORECASE):
                continue                              # glossary entry defining the word itself
            if len(s) > MAX_LEN:                      # long table rows: keep the part around the keyword
                m = MANDATORY.search(s)
                start = max(0, m.start() - 140)
                s = s[start:start + MAX_LEN].strip()
            key = normalise(s)
            if key in seen:                           # repeated headers/footers
                continue
            seen.add(key)
            found.append({"page": number, "text": s})
    return found


def _covered(statement: str, quotes: Iterable[str], texts: Iterable[str]) -> bool:
    st = normalise(statement)
    for q in quotes:
        nq = normalise(q)
        if len(nq) >= 12 and (nq in st or fuzz.partial_ratio(nq, st) >= 88):
            return True
    for t in texts:                                   # paraphrased requirement text
        if fuzz.token_set_ratio(normalise(t), st) >= 80:
            return True
    return False


def coverage(pages: List[str], quotes: List[str], texts: List[str]) -> Dict:
    statements = mandatory_statements(pages)
    unlisted = [s for s in statements if not _covered(s["text"], quotes, texts)]
    return {
        "mandatory_statements": len(statements),
        "covered": len(statements) - len(unlisted),
        "unlisted": unlisted,
    }
