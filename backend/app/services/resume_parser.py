"""Extract text from uploaded resume files (PDF and DOCX)."""

import logging
from io import BytesIO

logger = logging.getLogger(__name__)


async def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Extract text from PDF bytes using PyMuPDF."""
    import pymupdf

    doc = pymupdf.open(stream=file_bytes, filetype="pdf")
    pages = []
    for page in doc:
        pages.append(page.get_text())
    doc.close()
    return "\n".join(pages)


async def extract_text_from_docx(file_bytes: bytes) -> str:
    """Extract text from DOCX bytes using python-docx."""
    from docx import Document

    doc = Document(BytesIO(file_bytes))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n".join(paragraphs)


async def extract_text(filename: str, file_bytes: bytes) -> str:
    """Extract text from a resume file, routing by extension.

    Returns extracted text.
    Raises ValueError for unsupported formats or empty/image-only files.
    """
    lower = filename.lower()
    if lower.endswith(".pdf"):
        text = await extract_text_from_pdf(file_bytes)
    elif lower.endswith(".docx"):
        text = await extract_text_from_docx(file_bytes)
    else:
        raise ValueError(f"Unsupported file format: {filename}. Please upload a PDF or DOCX file.")

    if not text or len(text.strip()) < 50:
        raise ValueError(
            "Could not extract text from this file. "
            "If this is a scanned/image-based PDF, please upload a text-based PDF or DOCX instead."
        )

    return text
