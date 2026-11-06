"""Text extraction from (digital, non-scanned) PDFs via pdfplumber."""

from __future__ import annotations

import io
from dataclasses import dataclass

import pdfplumber


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
        """Scanned PDF: (almost) no text layer."""
        return len(self.text.strip()) < 20

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
