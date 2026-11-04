"""Workspace API under /api/workspace: evidence items, collections, notes, dashboard, export.

Every route needs a signed-in scholar (`current_user`) and only sees that scholar's rows.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

import alerts
import board
import citations
import drafts
import load_control
import study
import workspace
from accounts_api import current_user
from db_pool import db

router = APIRouter(prefix="/api/workspace", tags=["workspace"])


class _Context:
    """Set by api_server: `search(query, book_ids)` runs the search pipeline (saved-search alerts need it)."""

    def __init__(self):
        self.search = None


context = _Context()
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


class NodeIn(BaseModel):
    kind: str
    text: str = Field(default="", max_length=board.CLAIM_MAX * 2)
    evidence_id: Optional[int] = None
    note_id: Optional[int] = None
    concept_key: Optional[str] = Field(default=None, max_length=400)
    x: Optional[float] = None
    y: Optional[float] = None


class NodePatch(BaseModel):
    text: Optional[str] = Field(default=None, max_length=board.CLAIM_MAX * 2)
    x: Optional[float] = None
    y: Optional[float] = None


class EdgeIn(BaseModel):
    source_id: int
    target_id: int
    relation: str = Field(max_length=20)
    note: str = Field(default="", max_length=600)


class SeedIn(BaseModel):
    concept_id: int


class CardIn(BaseModel):
    evidence_id: int
    front: Optional[str] = Field(default=None, max_length=study.MAX_FRONT * 2)
    project_id: Optional[int] = None


class CardPatch(BaseModel):
    front: Optional[str] = Field(default=None, max_length=study.MAX_FRONT * 2)
    suspended: Optional[bool] = None
    project_id: Optional[int] = None


class BulkCards(BaseModel):
    collection_id: Optional[int] = None
    project_id: Optional[int] = None


class ReviewIn(BaseModel):
    grade: int = Field(ge=0, le=3)


class SavedSearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=600)
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


# --- research board -----------------------------------------------------------------------------------------------


def _board_fail(exc: board.BoardError):
    raise HTTPException(exc.status, str(exc)) from exc


@router.get("/projects/{project_id}/board")
def get_board(project_id: int, user: User = Depends(current_user)):
    try:
        with db() as connection:
            return board.get_board(connection, user["id"], project_id)
    except board.BoardError as exc:
        _board_fail(exc)


@router.post("/projects/{project_id}/board/nodes")
def add_board_node(project_id: int, payload: NodeIn, user: User = Depends(current_user)):
    try:
        with db() as connection:
            node = board.add_node(
                connection, user["id"], project_id, payload.kind, payload.text, payload.evidence_id, payload.note_id,
                payload.concept_key, payload.x, payload.y,
            )
            connection.commit()
    except board.BoardError as exc:
        _board_fail(exc)
    return node


@router.patch("/projects/{project_id}/board/nodes/{node_id}")
def update_board_node(project_id: int, node_id: int, payload: NodePatch, user: User = Depends(current_user)):
    try:
        with db() as connection:
            node = board.update_node(connection, user["id"], project_id, node_id, payload.text, payload.x, payload.y)
            connection.commit()
    except board.BoardError as exc:
        _board_fail(exc)
    return node


@router.delete("/projects/{project_id}/board/nodes/{node_id}")
def delete_board_node(project_id: int, node_id: int, user: User = Depends(current_user)):
    try:
        with db() as connection:
            _found(board.delete_node(connection, user["id"], project_id, node_id))
            connection.commit()
    except board.BoardError as exc:
        _board_fail(exc)
    return {"ok": True}


@router.post("/projects/{project_id}/board/edges")
def add_board_edge(project_id: int, payload: EdgeIn, user: User = Depends(current_user)):
    try:
        with db() as connection:
            edge = board.add_edge(connection, user["id"], project_id, payload.source_id, payload.target_id, payload.relation, payload.note)
            connection.commit()
    except board.BoardError as exc:
        _board_fail(exc)
    return edge


@router.delete("/projects/{project_id}/board/edges/{edge_id}")
def delete_board_edge(project_id: int, edge_id: int, user: User = Depends(current_user)):
    try:
        with db() as connection:
            _found(board.delete_edge(connection, user["id"], project_id, edge_id))
            connection.commit()
    except board.BoardError as exc:
        _board_fail(exc)
    return {"ok": True}


@router.post("/projects/{project_id}/board/seed")
def seed_board(project_id: int, payload: SeedIn, user: User = Depends(current_user)):
    """Put a concept and its strongest neighbours on the board."""
    try:
        with db() as connection:
            result = board.seed_concept(connection, user["id"], project_id, payload.concept_id)
            connection.commit()
            return {**result, **board.get_board(connection, user["id"], project_id)}
    except board.BoardError as exc:
        _board_fail(exc)


@router.post("/projects/{project_id}/board/outline")
def board_outline(project_id: int, user: User = Depends(current_user)):
    """A new draft made from the board: claims as headings with their evidence as cited block quotations."""
    try:
        with db() as connection:
            draft = board.create_outline_draft(connection, user["id"], project_id)
            connection.commit()
    except board.BoardError as exc:
        _board_fail(exc)
    return draft


# --- study: flashcards ----------------------------------------------------------------------------------------------


def _study_fail(exc: study.StudyError):
    raise HTTPException(exc.status, str(exc)) from exc


@router.post("/cards")
def create_card(payload: CardIn, user: User = Depends(current_user)):
    try:
        with db() as connection:
            card = study.create_card(connection, user["id"], payload.evidence_id, payload.front, payload.project_id)
            connection.commit()
    except study.StudyError as exc:
        _study_fail(exc)
    return card


@router.post("/cards/bulk")
def create_cards_bulk(payload: BulkCards, user: User = Depends(current_user)):
    """Cards with automatic fronts for the definitions in a collection or project."""
    try:
        with db() as connection:
            result = study.create_cards_bulk(connection, user["id"], payload.collection_id, payload.project_id)
            connection.commit()
    except study.StudyError as exc:
        _study_fail(exc)
    return result


@router.get("/cards")
def list_cards(
    project_id: Optional[int] = None, limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
):
    with db() as connection:
        return study.list_cards(connection, user["id"], project_id, limit, offset)


@router.patch("/cards/{card_id}")
def update_card(card_id: int, payload: CardPatch, user: User = Depends(current_user)):
    try:
        with db() as connection:
            card = _found(
                study.update_card(
                    connection, user["id"], card_id, payload.front, payload.suspended, payload.project_id,
                    set_project="project_id" in payload.model_fields_set,
                )
            )
            connection.commit()
    except study.StudyError as exc:
        _study_fail(exc)
    return card


@router.delete("/cards/{card_id}")
def delete_card(card_id: int, user: User = Depends(current_user)):
    with db() as connection:
        _found(study.delete_card(connection, user["id"], card_id))
        connection.commit()
    return {"ok": True}


@router.get("/study/due")
def due_cards(project_id: Optional[int] = None, limit: int = Query(default=20, ge=1, le=100), user: User = Depends(current_user)):
    with db() as connection:
        return {"cards": study.due_cards(connection, user["id"], project_id, limit), "stats": study.stats(connection, user["id"], project_id)}


@router.post("/cards/{card_id}/review")
def review_card(card_id: int, payload: ReviewIn, user: User = Depends(current_user)):
    try:
        with db() as connection:
            card = _found(study.review_card(connection, user["id"], card_id, payload.grade))
            connection.commit()
            return {"card": card, "stats": study.stats(connection, user["id"])}
    except study.StudyError as exc:
        _study_fail(exc)


@router.get("/study/stats")
def study_stats(project_id: Optional[int] = None, user: User = Depends(current_user)):
    with db() as connection:
        return study.stats(connection, user["id"], project_id)


# --- saved searches and notifications -------------------------------------------------------------------------------------


def _alert_fail(exc: alerts.AlertError):
    raise HTTPException(exc.status, str(exc)) from exc


@router.get("/saved-searches")
def list_saved_searches(user: User = Depends(current_user)):
    with db() as connection:
        return {"searches": alerts.list_searches(connection, user["id"])}


@router.post("/saved-searches")
def save_search(payload: SavedSearchIn, user: User = Depends(current_user)):
    try:
        with db() as connection:
            search = alerts.save_search(connection, user["id"], payload.query, payload.project_id)
            connection.commit()
    except alerts.AlertError as exc:
        _alert_fail(exc)
    return search


@router.delete("/saved-searches/{search_id}")
def delete_saved_search(search_id: int, user: User = Depends(current_user)):
    with db() as connection:
        _found(alerts.delete_search(connection, user["id"], search_id))
        connection.commit()
    return {"ok": True}


@router.post("/saved-searches/check")
async def check_saved_searches(user: User = Depends(current_user)):
    """Re-run the saved searches the library has changed under (a few per call), through the search queue; returns the
    new notifications. Cheap when nothing changed."""
    def collect():
        with db() as connection:
            current = alerts.library_fingerprint(connection)
            return current, alerts.due_searches(connection, user["id"], current)

    fingerprint, due = await run_in_threadpool(collect)
    checked = 0

    async def run_one(search) -> Optional[Dict[str, Any]]:
        if search["book_ids"] == []:  # a project without books has nothing to search
            return {"results": []}
        return await load_control.gate().run(lambda: run_in_threadpool(context.search, search["query"], search["book_ids"]))

    if due and context.search is not None:
        for search in due:
            try:
                results = await run_one(search)
            except load_control.Busy:
                break  # the server is busy: try again at the next visit
            except Exception as exc:  # pylint: disable=broad-except
                print(f"[OSISS] Saved search check failed: {exc}")
                continue

            def record(found=results, search_id=search["id"]):
                with db() as connection:
                    alerts.record_result(connection, user["id"], search_id, fingerprint, (found or {}).get("results", []))
                    connection.commit()

            await run_in_threadpool(record)
            checked += 1

    def read():
        with db() as connection:
            return alerts.list_notifications(connection, user["id"], unread_only=True)

    return {"checked": checked, "pending": max(0, len(due) - checked), "notifications": await run_in_threadpool(read)}


@router.get("/notifications")
def list_notifications(unread_only: bool = False, user: User = Depends(current_user)):
    with db() as connection:
        return {"notifications": alerts.list_notifications(connection, user["id"], unread_only)}


@router.post("/notifications/read")
def read_notifications(notification_id: Optional[int] = None, user: User = Depends(current_user)):
    """Marks one notification (or, without an id, all of them) as read."""
    with db() as connection:
        changed = alerts.mark_read(connection, user["id"], notification_id)
        connection.commit()
    return {"changed": changed}


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
