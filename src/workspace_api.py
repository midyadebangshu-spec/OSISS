"""Workspace API under /api/workspace: evidence items, collections, notes, dashboard, export.

Every route needs a signed-in scholar (`current_user`) and only sees that scholar's rows.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

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


class CollectionIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = Field(default=None, max_length=4000)


class NoteIn(BaseModel):
    title: Optional[str] = Field(default=None, max_length=400)
    body: Optional[str] = Field(default=None, max_length=workspace.MAX_NOTE * 2)
    evidence_id: Optional[int] = None


class ActivityIn(BaseModel):
    kind: str
    ref: str = Field(max_length=300)
    label: str = Field(default="", max_length=300)
    book_id: Optional[int] = None


@router.get("/dashboard")
def dashboard(user: User = Depends(current_user)):
    with db() as connection:
        return workspace.dashboard(connection, user["id"])


@router.post("/activity")
def record_activity(payload: ActivityIn, user: User = Depends(current_user)):
    try:
        with db() as connection:
            workspace.record_activity(connection, user["id"], payload.kind, payload.ref, payload.label, payload.book_id)
            connection.commit()
    except workspace.WorkspaceError as exc:
        _fail(exc)
    return {"ok": True}


# --- evidence -----------------------------------------------------------------------------------------------------


@router.post("/evidence")
def save_evidence(payload: EvidenceIn, user: User = Depends(current_user)):
    try:
        with db() as connection:
            item = workspace.save_evidence(connection, user["id"], payload.model_dump(exclude={"collection_id"}))
            if payload.collection_id is not None:
                if not workspace.add_to_collection(connection, user["id"], payload.collection_id, item["id"]):
                    raise HTTPException(404, "Unknown collection.")
                item = workspace.get_evidence(connection, user["id"], item["id"])
            connection.commit()
    except workspace.WorkspaceError as exc:
        _fail(exc)
    return item


@router.get("/evidence")
def list_evidence(
    collection_id: Optional[int] = None,
    kind: Optional[str] = None,
    q: Optional[str] = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
):
    with db() as connection:
        return workspace.list_evidence(connection, user["id"], collection_id, kind, q, limit, offset)


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
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
):
    with db() as connection:
        return {"notes": workspace.list_notes(connection, user["id"], q, concept, limit, offset)}


@router.post("/notes")
def create_note(payload: NoteIn, user: User = Depends(current_user)):
    try:
        with db() as connection:
            note = workspace.create_note(
                connection, user["id"], payload.title or "", payload.body or "", payload.evidence_id
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


# --- export -------------------------------------------------------------------------------------------------------


@router.get("/export")
def export_all(user: User = Depends(current_user)):
    with db() as connection:
        data = workspace.export_all(connection, user["id"])
    return data
