"""Text extraction from (digital, non-scanned) PDFs via pdfplumber."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

import pdfplumber

_WORD = re.compile(r"[A-Za-zÄÖÜäöüß]{2,}(?:[-.'][A-Za-zÄÖÜäöüß]+)*\.?")  # Rechnungs-Nr.
_CODE = re.compile(r"(?=[0-9A-Z]*\d)[0-9A-Z]+(?:[-/.,:][0-9A-Z]+)*")  # 03.11.2026, S26/545-10, 1.744,00


def text_quality(text: str) -> float:
    """Share of tokens that look like words, numbers or codes (garbled scanner OCR scores low)."""
    tokens = [t.strip(".,:;()[]\"'«»„“”€%") for t in text.split()]
    tokens = [t for t in tokens if any(ch.isalnum() for ch in t)]  # "/" and "-" alone don't count
    if not tokens:
        return 0.0
    good = sum(1 for t in tokens if _WORD.fullmatch(t) or _CODE.fullmatch(t))
    return good / len(tokens)


@dataclass
class Word:
    text: str
    x0: float
    top: float
    x1: float
    bottom: float


@dataclass
class PageWords:
    page: int  # 1-based
    width: float
    height: float
    words: list[Word]


@dataclass
class PdfText:
    text: str  # all pages, layout-ordered lines
    data: bytes  # raw PDF, kept so regions can be cropped lazily
    ocr: bool = False  # True if the text layer was produced by OCR

    @classmethod
    def from_bytes(cls, data: bytes, *, ocr: bool = False) -> PdfText:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            text = "\n".join((page.extract_text() or "") for page in pdf.pages)
        return cls(text=text, data=data, ocr=ocr)

    @property
    def needs_ocr(self) -> bool:
        """At least one page is a scan (see scan_pages)."""
        return bool(self.scan_pages())

    def scan_pages(self) -> list[int]:
        """1-based numbers of pages that need text recognition.

        A page counts as scanned when it has (almost) no text, or when an image
        covers most of it and the text on it is sparse or garbled – scanners often
        add a stamp ("Scan 1/2") or their own poor text layer, which must not stop
        the OCR. Digital pages with real text are left alone.
        """
        pages = []
        with pdfplumber.open(io.BytesIO(self.data)) as pdf:
            for n, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                chars = len("".join(text.split()))
                area = float(page.width * page.height) or 1.0
                covered = sum(
                    max(0.0, min(img["x1"], page.width) - max(img["x0"], 0))
                    * max(0.0, min(img["bottom"], page.height) - max(img["top"], 0))
                    for img in page.images
                )
                scanned_look = covered / area >= 0.5
                if chars < 20 and (page.images or len("".join(self.text.split())) < 20):
                    # no text: a scan (blank pages of digital PDFs without images are skipped)
                    pages.append(n)
                elif scanned_look and (chars < 200 or text_quality(text) < 0.75):
                    pages.append(n)
        return pages

    def pages(self, max_pages: int = 10) -> list[PageWords]:
        """Words with positions (PDF points, origin top-left) for the click-to-link preview."""
        result: list[PageWords] = []
        with pdfplumber.open(io.BytesIO(self.data)) as pdf:
            for n, page in enumerate(pdf.pages[:max_pages], start=1):
                words = [
                    Word(text=w["text"], x0=w["x0"], top=w["top"], x1=w["x1"], bottom=w["bottom"])
                    for w in page.extract_words(keep_blank_chars=False, use_text_flow=False)
                ]
                result.append(PageWords(page=n, width=float(page.width), height=float(page.height), words=words))
        return result

    def region_text(self, bbox: tuple[float, float, float, float]) -> str:
        """Text inside (x0, top, x1, bottom) on page 1, in PDF points."""
        with pdfplumber.open(io.BytesIO(self.data)) as pdf:
            if not pdf.pages:
                return ""
            page = pdf.pages[0]
            x0, top, x1, bottom = bbox
            box = (max(0, x0), max(0, top), min(page.width, x1), min(page.height, bottom))
            return page.within_bbox(box).extract_text() or ""
