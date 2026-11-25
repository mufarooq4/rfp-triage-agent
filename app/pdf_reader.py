"""Turn an RFP PDF into page-tagged text the model can cite.

Every page is wrapped in a ``=== PDF PAGE n ===`` marker. The model is told to
cite those numbers, and the verifier later checks each quote against the same
page text -- so the page numbers are the backbone of the evidence trail.

Strike-through detection: agencies often amend a document by striking out the
old value and printing the new one next to it ("~~January 8~~ January 15").
Plain text extraction loses the line, so both dates come out side by side and a
model can pick the wrong one. We look for thin horizontal lines drawn through
the MIDDLE of words (an underline sits at the bottom, a table border between
rows) and wrap those words as ``[STRUCK OUT: ...]`` before the model sees them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Tuple

import pymupdf as fitz  # PyMuPDF


class PDFError(ValueError):
    """Raised for files we cannot read (not a PDF, scanned image, too long)."""


@dataclass
class ParsedPDF:
    pages: List[str]
    page_count: int
    char_count: int
    metadata: dict = field(default_factory=dict)
    struck: List[Tuple[int, str]] = field(default_factory=list)   # (page number, struck text)

    def tagged_text(self) -> str:
        return "\n\n".join(
            f"=== PDF PAGE {i} ===\n{text.strip()}" for i, text in enumerate(self.pages, start=1)
        )


def _struck_phrases(page) -> List[str]:
    """Return runs of words on this page that have a line drawn through their middle."""
    lines = []
    for d in page.get_drawings():
        r = d["rect"]
        if r.height <= 1.6 and r.width >= 4:          # thin horizontal stroke or filled bar
            lines.append(r)
    if not lines:
        return []

    words = page.get_text("words", sort=True)         # (x0, y0, x1, y1, text, block, line, word_no)
    struck_flags = []
    for x0, y0, x1, y1, text, block, line, _ in words:
        h = y1 - y0
        mid_lo, mid_hi = y0 + 0.3 * h, y0 + 0.75 * h
        width = max(x1 - x0, 0.1)
        hit = False
        for r in lines:
            y = (r.y0 + r.y1) / 2
            overlap = min(x1, r.x1) - max(x0, r.x0)
            if mid_lo <= y <= mid_hi and overlap >= 0.6 * width:
                hit = True
                break
        struck_flags.append((hit, block, line, text))

    phrases, current, key = [], [], None
    for hit, block, line, text in struck_flags:
        if hit and (key is None or key == (block, line)):
            current.append(text)
            key = (block, line)
        else:
            if current:
                phrases.append(" ".join(current))
            current, key = ([text], (block, line)) if hit else ([], None)
    if current:
        phrases.append(" ".join(current))
    return phrases


def _mark(text: str, phrase: str) -> str:
    pattern = r"\s+".join(re.escape(w) for w in phrase.split())
    return re.sub(pattern, lambda m: f"[STRUCK OUT: {m.group(0)}]", text, count=1)


def read_pdf(data: bytes, max_pages: int = 150) -> ParsedPDF:
    if not data or not data[:1024].lstrip().startswith(b"%PDF"):
        raise PDFError("That file is not a PDF.")
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # corrupt file
        raise PDFError(f"Could not open the PDF: {exc}") from exc

    if doc.needs_pass:
        raise PDFError("The PDF is password-protected.")
    if doc.page_count == 0:
        raise PDFError("The PDF has no pages.")
    if doc.page_count > max_pages:
        raise PDFError(f"The PDF has {doc.page_count} pages; the limit is {max_pages}.")

    pages: List[str] = []
    struck: List[Tuple[int, str]] = []
    for number, page in enumerate(doc, start=1):
        # sort=True gives natural reading order for multi-column layouts and tables
        text = page.get_text("text", sort=True)
        for phrase in _struck_phrases(page):
            text = _mark(text, phrase)
            struck.append((number, phrase))
        pages.append(text)
    meta = {k: v for k, v in (doc.metadata or {}).items() if v}
    doc.close()

    chars = sum(len(p.strip()) for p in pages)
    if chars < 40 * len(pages):
        raise PDFError(
            "This PDF has almost no extractable text -- it looks like a scanned image. "
            "Please upload a text-based PDF."
        )
    return ParsedPDF(pages=pages, page_count=len(pages), char_count=chars, metadata=meta, struck=struck)
