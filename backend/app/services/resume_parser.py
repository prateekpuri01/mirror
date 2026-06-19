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
    """Extract text from DOCX bytes using python-docx.

    Walks both top-level paragraphs AND every table cell (recursively, since
    cells can contain nested tables). Two-column resumes and Mirror's own
    ``two_column``/``banner`` presets put most content inside tables — a
    paragraphs-only pass would return ~empty text and fail the extractor
    threshold. Also pulls header paragraphs since stylized designs sometimes
    place the name there.
    """
    from docx import Document
    from docx.document import Document as _Document
    from docx.oxml.ns import qn
    from docx.table import Table, _Cell
    from docx.text.paragraph import Paragraph

    doc = Document(BytesIO(file_bytes))

    def iter_block_items(parent):
        if isinstance(parent, _Document):
            parent_elm = parent.element.body
        elif isinstance(parent, _Cell):
            parent_elm = parent._tc
        else:
            return
        for child in parent_elm.iterchildren():
            if child.tag == qn("w:p"):
                yield Paragraph(child, parent)
            elif child.tag == qn("w:tbl"):
                yield Table(child, parent)

    def extract_from(parent) -> list[str]:
        lines: list[str] = []
        for block in iter_block_items(parent):
            if isinstance(block, Paragraph):
                txt = block.text.strip()
                if txt:
                    lines.append(txt)
            elif isinstance(block, Table):
                for row in block.rows:
                    for cell in row.cells:
                        lines.extend(extract_from(cell))
        return lines

    body_lines = extract_from(doc)

    # Header paragraphs (where styled designs sometimes place the name).
    # Prepend so the name surfaces near the top of the extracted text and
    # the name-fallback heuristic can find it.
    header_lines: list[str] = []
    for section in doc.sections:
        for hdr_para in section.header.paragraphs:
            txt = hdr_para.text.strip()
            if txt:
                header_lines.append(txt)

    return "\n".join(header_lines + body_lines)


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
