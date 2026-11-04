"""Workspace API under /api/workspace: evidence items, collections, notes, dashboard, export.

Every route needs a signed-in scholar (`current_user`) and only sees that scholar's rows.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

import citations
import drafts
import workspace
from accounts_api import current_user
from db_pool import db

router = APIRouter(prefix="/api/workspace", tags=["workspace"])
User = Dict[str, Any]


def _fail(exc: workspace.WorkspaceError):
    raise HTTPException(exc.status, str(exc)) from exc


def _found(value):
    if value is None or value is False:
        raise HTTPException(404, "Not found.")
    return value


class EvidenceIn(BaseModel):
    kind: str
    book_id: Optional[int] = None
    page_number: Optional[int] = Field(default=None, ge=1)
    quote: str = Field(default="", max_length=20000)
    concept_key: Optional[str] = Field(default=None, max_length=200)
    meta: Dict[str, Any] = Field(default_factory=dict)
    collection_id: Optional[int] = None  # also put the new item into this collection
    project_id: Optional[int] = None  # and/or attach it to this project


class CollectionIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = Field(default=None, max_length=4000)


class NoteIn(BaseModel):
    title: Optional[str] = Field(default=None, max_length=400)
    body: Optional[str] = Field(default=None, max_length=workspace.MAX_NOTE * 2)
    evidence_id: Optional[int] = None
    project_id: Optional[int] = None


class ActivityIn(BaseModel):
    kind: str
    ref: str = Field(max_length=300)
    label: str = Field(default="", max_length=300)
    book_id: Optional[int] = None
    page_number: Optional[int] = Field(default=None, ge=1)


class ProjectIn(BaseModel):
    title: Optional[str] = Field(default=None, max_length=400)
    question: Optional[str] = Field(default=None, max_length=2000)
    description: Optional[str] = Field(default=None, max_length=8000)
    book_ids: List[int] = Field(default_factory=list, max_length=200)


class DraftIn(BaseModel):
    title: Optional[str] = Field(default=None, max_length=400)
    body: Optional[str] = Field(default=None, max_length=drafts.MAX_BODY * 2)
    style: Optional[str] = Field(default=None, max_length=20)
    project_id: Optional[int] = None


class BookIds(BaseModel):
    book_ids: List[int] = Field(min_length=1, max_length=200)


@router.get("/dashboard")
def dashboard(user: User = Depends(current_user)):
    with db() as connection:
        return workspace.dashboard(connection, user["id"])


@router.post("/activity")
def record_activity(payload: ActivityIn, user: User = Depends(current_user)):
    try:
        with db() as connection:
            workspace.record_activity(
                connection, user["id"], payload.kind, payload.ref, payload.label, payload.book_id, payload.page_number
            )
            connection.commit()
    except workspace.WorkspaceError as exc:
        _fail(exc)
    return {"ok": True}


# --- evidence -----------------------------------------------------------------------------------------------------


@router.post("/evidence")
def save_evidence(payload: EvidenceIn, user: User = Depends(current_user)):
    try:
        with db() as connection:
            item = workspace.save_evidence(
                connection, user["id"], payload.model_dump(exclude={"collection_id", "project_id"})
            )
            if payload.collection_id is not None:
                if not workspace.add_to_collection(connection, user["id"], payload.collection_id, item["id"]):
                    raise HTTPException(404, "Unknown collection.")
            if payload.project_id is not None:
                if not workspace.attach_evidence(connection, user["id"], payload.project_id, item["id"]):
                    raise HTTPException(404, "Unknown project.")
            item = workspace.get_evidence(connection, user["id"], item["id"])
            connection.commit()
    except workspace.WorkspaceError as exc:
        _fail(exc)
    return item


@router.get("/evidence")
def list_evidence(
    collection_id: Optional[int] = None,
    project_id: Optional[int] = None,
    book_id: Optional[int] = None,
    page_number: Optional[int] = None,
    kind: Optional[str] = None,
    q: Optional[str] = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
):
    with db() as connection:
        return workspace.list_evidence(
            connection, user["id"], collection_id, kind, q, limit, offset, project_id, book_id, page_number
        )


@router.delete("/evidence/{evidence_id}")
def delete_evidence(evidence_id: int, user: User = Depends(current_user)):
    with db() as connection:
        _found(workspace.delete_evidence(connection, user["id"], evidence_id))
        connection.commit()
    return {"ok": True}


# --- collections --------------------------------------------------------------------------------------------------


@router.get("/collections")
def list_collections(user: User = Depends(current_user)):
    with db() as connection:
        return {"collections": workspace.list_collections(connection, user["id"])}


@router.post("/collections")
def create_collection(payload: CollectionIn, user: User = Depends(current_user)):
    try:
        with db() as connection:
            collection = workspace.create_collection(connection, user["id"], payload.name or "", payload.description or "")
            connection.commit()
    except workspace.WorkspaceError as exc:
        _fail(exc)
    return collection


@router.get("/collections/{collection_id}")
def get_collection(collection_id: int, user: User = Depends(current_user)):
    with db() as connection:
        collection = _found(workspace.get_collection(connection, user["id"], collection_id))
        items = workspace.list_evidence(connection, user["id"], collection_id=collection_id, limit=200)
    return {**collection, "items": items["items"]}


@router.patch("/collections/{collection_id}")
def update_collection(collection_id: int, payload: CollectionIn, user: User = Depends(current_user)):
    try:
        with db() as connection:
            collection = _found(
                workspace.update_collection(connection, user["id"], collection_id, payload.name, payload.description)
            )
            connection.commit()
    except workspace.WorkspaceError as exc:
        _fail(exc)
    return collection


@router.delete("/collections/{collection_id}")
def delete_collection(collection_id: int, user: User = Depends(current_user)):
    with db() as connection:
        _found(workspace.delete_collection(connection, user["id"], collection_id))
        connection.commit()
    return {"ok": True}


@router.put("/collections/{collection_id}/items/{evidence_id}")
def add_to_collection(collection_id: int, evidence_id: int, user: User = Depends(current_user)):
    with db() as connection:
        _found(workspace.add_to_collection(connection, user["id"], collection_id, evidence_id))
        connection.commit()
    return {"ok": True}


@router.delete("/collections/{collection_id}/items/{evidence_id}")
def remove_from_collection(collection_id: int, evidence_id: int, user: User = Depends(current_user)):
    with db() as connection:
        _found(workspace.remove_from_collection(connection, user["id"], collection_id, evidence_id))
        connection.commit()
    return {"ok": True}


# --- notes --------------------------------------------------------------------------------------------------------


@router.get("/notes")
def list_notes(
    q: Optional[str] = Query(default=None, max_length=200),
    concept: Optional[str] = Query(default=None, max_length=120),
    project_id: Optional[int] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
):
    with db() as connection:
        return {"notes": workspace.list_notes(connection, user["id"], q, concept, limit, offset, project_id)}


@router.post("/notes")
def create_note(payload: NoteIn, user: User = Depends(current_user)):
    try:
        with db() as connection:
            note = workspace.create_note(
                connection, user["id"], payload.title or "", payload.body or "", payload.evidence_id, payload.project_id
            )
            connection.commit()
    except workspace.WorkspaceError as exc:
        _fail(exc)
    return note


@router.get("/notes/{note_id}")
def get_note(note_id: int, user: User = Depends(current_user)):
    with db() as connection:
        return _found(workspace.get_note(connection, user["id"], note_id))


@router.patch("/notes/{note_id}")
def update_note(note_id: int, payload: NoteIn, user: User = Depends(current_user)):
    try:
        with db() as connection:
            note = _found(workspace.update_note(connection, user["id"], note_id, payload.title, payload.body))
            connection.commit()
    except workspace.WorkspaceError as exc:
        _fail(exc)
    return note


@router.delete("/notes/{note_id}")
def delete_note(note_id: int, user: User = Depends(current_user)):
    with db() as connection:
        _found(workspace.delete_note(connection, user["id"], note_id))
        connection.commit()
    return {"ok": True}


# --- projects -----------------------------------------------------------------------------------------------------


@router.get("/projects")
def list_projects(user: User = Depends(current_user)):
    with db() as connection:
        return {"projects": workspace.list_projects(connection, user["id"])}


@router.post("/projects")
def create_project(payload: ProjectIn, user: User = Depends(current_user)):
    try:
        with db() as connection:
            project = workspace.create_project(
                connection, user["id"], payload.title or "", payload.question or "", payload.description or "",
                payload.book_ids,
            )
            connection.commit()
    except workspace.WorkspaceError as exc:
        _fail(exc)
    return project


@router.get("/projects/{project_id}")
def get_project(project_id: int, user: User = Depends(current_user)):
    with db() as connection:
        return _found(workspace.get_project(connection, user["id"], project_id))


@router.patch("/projects/{project_id}")
def update_project(project_id: int, payload: ProjectIn, user: User = Depends(current_user)):
    try:
        with db() as connection:
            project = _found(
                workspace.update_project(
                    connection, user["id"], project_id, payload.title, payload.question, payload.description
                )
            )
            connection.commit()
    except workspace.WorkspaceError as exc:
        _fail(exc)
    return project


@router.delete("/projects/{project_id}")
def delete_project(project_id: int, user: User = Depends(current_user)):
    with db() as connection:
        _found(workspace.delete_project(connection, user["id"], project_id))
        connection.commit()
    return {"ok": True}


@router.post("/projects/{project_id}/books")
def add_project_books(project_id: int, payload: BookIds, user: User = Depends(current_user)):
    try:
        with db() as connection:
            _found(workspace.add_project_books(connection, user["id"], project_id, payload.book_ids))
            connection.commit()
            return workspace.get_project(connection, user["id"], project_id)
    except workspace.WorkspaceError as exc:
        _fail(exc)


@router.delete("/projects/{project_id}/books/{book_id}")
def remove_project_book(project_id: int, book_id: int, user: User = Depends(current_user)):
    with db() as connection:
        _found(workspace.remove_project_book(connection, user["id"], project_id, book_id))
        connection.commit()
    return {"ok": True}


@router.put("/projects/{project_id}/evidence/{evidence_id}")
def attach_evidence(project_id: int, evidence_id: int, user: User = Depends(current_user)):
    with db() as connection:
        _found(workspace.attach_evidence(connection, user["id"], project_id, evidence_id))
        connection.commit()
    return {"ok": True}


@router.delete("/projects/{project_id}/evidence/{evidence_id}")
def detach_evidence(project_id: int, evidence_id: int, user: User = Depends(current_user)):
    with db() as connection:
        _found(workspace.detach_evidence(connection, user["id"], project_id, evidence_id))
        connection.commit()
    return {"ok": True}


# --- writing studio -----------------------------------------------------------------------------------------------


def _draft_fail(exc: drafts.DraftError):
    raise HTTPException(exc.status, str(exc)) from exc


@router.get("/drafts")
def list_drafts(project_id: Optional[int] = None, user: User = Depends(current_user)):
    with db() as connection:
        return {"drafts": drafts.list_drafts(connection, user["id"], project_id)}


@router.post("/drafts")
def create_draft(payload: DraftIn, user: User = Depends(current_user)):
    try:
        with db() as connection:
            draft = drafts.create_draft(
                connection, user["id"], payload.title or "", payload.body or "", payload.style or "apa", payload.project_id
            )
            connection.commit()
    except drafts.DraftError as exc:
        _draft_fail(exc)
    return draft


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: int, user: User = Depends(current_user)):
    with db() as connection:
        return _found(drafts.get_draft(connection, user["id"], draft_id))


@router.patch("/drafts/{draft_id}")
def update_draft(draft_id: int, payload: DraftIn, user: User = Depends(current_user)):
    try:
        with db() as connection:
            draft = _found(
                drafts.update_draft(
                    connection, user["id"], draft_id, payload.title, payload.body, payload.style, payload.project_id,
                    set_project="project_id" in payload.model_fields_set,
                )
            )
            connection.commit()
    except drafts.DraftError as exc:
        _draft_fail(exc)
    return draft


@router.delete("/drafts/{draft_id}")
def delete_draft(draft_id: int, user: User = Depends(current_user)):
    with db() as connection:
        _found(drafts.delete_draft(connection, user["id"], draft_id))
        connection.commit()
    return {"ok": True}


@router.get("/drafts/{draft_id}/render")
def render_draft(draft_id: int, style: Optional[str] = None, user: User = Depends(current_user)):
    try:
        with db() as connection:
            draft = _found(drafts.get_draft(connection, user["id"], draft_id))
            return drafts.render_draft(connection, user["id"], draft, style)
    except drafts.DraftError as exc:
        _draft_fail(exc)


@router.post("/drafts/{draft_id}/check")
def check_draft(draft_id: int, user: User = Depends(current_user)):
    """Compare every quotation in the draft with the page it cites."""
    with db() as connection:
        draft = _found(drafts.get_draft(connection, user["id"], draft_id))
        return drafts.check_draft(connection, user["id"], draft)


@router.get("/drafts/{draft_id}/export")
def export_draft(draft_id: int, format: str = Query(pattern="^(docx|tex|md)$"), style: Optional[str] = None,
                 user: User = Depends(current_user)):
    import exporters  # pylint: disable=import-outside-toplevel

    try:
        with db() as connection:
            draft = _found(drafts.get_draft(connection, user["id"], draft_id))
            rendered = drafts.render_draft(connection, user["id"], draft, style)
    except drafts.DraftError as exc:
        _draft_fail(exc)
    data, media_type, filename = exporters.export(rendered, format)
    return Response(
        content=data, media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote_name(filename)}"},
    )


def quote_name(name: str) -> str:
    from urllib.parse import quote  # pylint: disable=import-outside-toplevel

    return quote(name)


# --- references ---------------------------------------------------------------------------------------------------


@router.get("/references")
def references(
    style: str = Query(default="apa", pattern="^(apa|mla|chicago|ieee)$"),
    project_id: Optional[int] = None,
    format: str = Query(default="json", pattern="^(json|bibtex|ris)$"),
    user: User = Depends(current_user),
):
    """The works the scholar uses, formatted in a style; `format=bibtex|ris` downloads a file."""
    with db() as connection:
        if project_id is not None:
            _found(workspace.get_project(connection, user["id"], project_id, detail=False))
        books = drafts.reference_books(connection, user["id"], project_id)
    if format == "bibtex":
        return Response(drafts.bibtex_file(books), media_type="application/x-bibtex; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename=osiss-references.bib"})
    if format == "ris":
        return Response(drafts.ris_file(books), media_type="application/x-research-info-systems; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename=osiss-references.ris"})
    return {
        "style": style, "styles": citations.STYLE_NAMES, "entries": drafts.reference_entries(books, style),
        "books": [{k: v for k, v in b.items() if k != "has_page_labels"} for b in books],
    }


# --- export -------------------------------------------------------------------------------------------------------


@router.get("/export")
def export_all(user: User = Depends(current_user)):
    with db() as connection:
        data = workspace.export_all(connection, user["id"])
    return data
