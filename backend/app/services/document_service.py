import copy
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, DocType


async def list_documents_for_job(session: AsyncSession, job_id: uuid.UUID) -> list[Document]:
    result = await session.execute(
        select(Document).where(Document.job_id == job_id).order_by(Document.created_at.desc())
    )
    return list(result.scalars().all())


async def get_document(session: AsyncSession, doc_id: uuid.UUID) -> Document | None:
    result = await session.execute(select(Document).where(Document.id == doc_id))
    return result.scalar_one_or_none()


async def update_document_markdown(
    session: AsyncSession, doc_id: uuid.UUID, content_markdown: str
) -> Document | None:
    doc = await get_document(session, doc_id)
    if doc is None:
        return None
    doc.content_markdown = content_markdown
    await session.commit()
    await session.refresh(doc)
    return doc


async def create_document(
    session: AsyncSession,
    job_id: uuid.UUID,
    doc_type: DocType,
    name: str,
    content_markdown: str | None = None,
    content_docx_path: str | None = None,
    content_json: dict | None = None,
) -> Document:
    """Create a new document record."""
    doc = Document(
        job_id=job_id,
        doc_type=doc_type,
        name=name,
        content_markdown=content_markdown,
        content_docx_path=content_docx_path,
        content_json=content_json,
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)
    return doc


async def delete_document(session: AsyncSession, doc_id: uuid.UUID) -> bool:
    doc = await get_document(session, doc_id)
    if doc is None:
        return False
    await session.delete(doc)
    await session.commit()
    return True


# ---------------------------------------------------------------------------
# Section-level JSON updates
# ---------------------------------------------------------------------------


def _set_nested(obj: dict, path: str, value: Any) -> None:
    """Set a value at a dotted path in a nested dict/list structure.

    Supports paths like "experience.rand.bullets.0" where numeric segments
    index into lists.
    """
    keys = path.split(".")
    for key in keys[:-1]:
        if isinstance(obj, list):
            obj = obj[int(key)]
        else:
            obj = obj[key]
    final = keys[-1]
    if isinstance(obj, list):
        obj[int(final)] = value
    else:
        obj[final] = value


def _get_nested(obj: dict, path: str) -> Any:
    """Get a value at a dotted path in a nested dict/list structure."""
    for key in path.split("."):
        if isinstance(obj, list):
            obj = obj[int(key)]
        else:
            obj = obj[key]
    return obj


async def update_resume_section(
    session: AsyncSession, doc_id: uuid.UUID, path: str, value: Any
) -> Document | None:
    """Update a single section in the resume JSON by dotted path."""
    doc = await get_document(session, doc_id)
    if doc is None or doc.content_json is None:
        return None

    updated = copy.deepcopy(doc.content_json)
    _set_nested(updated, path, value)
    doc.content_json = updated
    doc.version += 1
    await session.commit()
    await session.refresh(doc)
    return doc


async def update_resume_json(
    session: AsyncSession, doc_id: uuid.UUID, content_json: dict
) -> Document | None:
    """Full replacement of resume JSON."""
    doc = await get_document(session, doc_id)
    if doc is None:
        return None
    doc.content_json = content_json
    doc.version += 1
    await session.commit()
    await session.refresh(doc)
    return doc
