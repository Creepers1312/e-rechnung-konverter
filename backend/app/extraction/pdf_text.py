"""Text extraction from (digital, non-scanned) PDFs via pdfplumber."""

from __future__ import annotations

import io
from dataclasses import dataclass

import pdfplumber


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

    def region_text(self, bbox: tuple[float, float, float, float]) -> str:
        """Text inside (x0, top, x1, bottom) on page 1, in PDF points."""
        with pdfplumber.open(io.BytesIO(self.data)) as pdf:
            if not pdf.pages:
                return ""
            page = pdf.pages[0]
            x0, top, x1, bottom = bbox
            box = (max(0, x0), max(0, top), min(page.width, x1), min(page.height, bottom))
            return page.within_bbox(box).extract_text() or ""
