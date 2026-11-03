"""Context Explorer API: /api/concepts (search / browse), /api/concepts/{id}, /api/concepts/{a}/links/{b}.

Reads the graph `build_concepts.py` stores (see concept_store). Same access rule as /api/search (the path is under
the public prefixes in api_server). Romanized lookups ("protisoron") also try the Bengali / Hindi rewrites.
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query

import admin
import concept_store
import page_assets
from db_pool import db

router = APIRouter(prefix="/api/concepts", tags=["concepts"])


def _summary(item: Dict) -> Dict[str, Any]:
    return {key: item[key] for key in ("id", "label", "aliases", "book_count", "mention_count", "definition_count")}


def _romanized_forms(query: str) -> List[str]:
    try:
        from translit import query_mode, romanized_variants  # pylint: disable=import-outside-toplevel

        if query_mode(query) == "english":
            return []
        return [variant.text for variant in romanized_variants(query)]
    except Exception:  # pylint: disable=broad-except
        return []  # transliteration model missing: plain matching only


_matcher_cache: Dict[str, Any] = {"items": None, "matcher": None, "by_key": {}}


def concepts_in_text(texts: List[str], limit: int = 4, query: str = "") -> List[Dict[str, Any]]:
    """Concepts named in a search hit, for "explore across books" links: those the question names first, then the
    ones named most often; concepts nearly every book mentions ("water", "life") last."""
    try:
        items = concept_store.cached_index(db)
    except Exception:  # pylint: disable=broad-except
        return []
    if not items:
        return []
    if _matcher_cache["items"] is not items:
        from concepts import AliasMatcher, normalize  # pylint: disable=import-outside-toplevel

        aliases = {}
        for item in items:
            for alias in item["aliases"]:
                aliases[normalize(alias)] = str(item["id"])
        _matcher_cache.update(items=items, matcher=AliasMatcher(aliases), by_key={str(i["id"]): i for i in items})
    counts: Dict[str, int] = {}
    for text in texts:
        for key, _ in _matcher_cache["matcher"].find(text or ""):
            counts[key] = counts.get(key, 0) + 1
    found = [_matcher_cache["by_key"][key] for key in counts]
    found = [item for item in found if item["book_count"] >= 2] or found
    from concepts import normalize  # pylint: disable=import-outside-toplevel

    asked = normalize(query)

    def rank(item: Dict) -> tuple:
        in_query = any(alias and alias in asked for alias in item["aliases"])
        widespread = item["book_count"] > 12
        return (not in_query, widespread, -counts[str(item["id"])], -item["book_count"])

    found.sort(key=rank)
    return [{"id": item["id"], "label": item["label"], "book_count": item["book_count"]} for item in found[:limit]]


@router.get("")
def search_concepts(q: str = Query(default="", max_length=200), limit: int = Query(default=12, ge=1, le=60)):
    """Concepts matching `q`, or (without `q`) the concepts most books cover, for browsing."""
    try:
        items = concept_store.cached_index(db)
    except Exception as exc:  # pylint: disable=broad-except
        raise HTTPException(503, f"Concept graph unavailable: {exc}") from exc
    if not items:
        return {"query": q, "built": False, "concepts": []}
    query = q.strip()
    if not query:
        return {"query": "", "built": True, "concepts": [_summary(item) for item in concept_store.top_concepts(items, limit)]}
    matches = concept_store.rank_matches(query, items, limit=limit)
    if not matches:
        matches = concept_store.rank_matches(query, items, extra_forms=_romanized_forms(query), limit=limit)
    return {"query": query, "built": True, "concepts": [_summary(item) for item in matches]}


def _book_out(book: Dict) -> Dict[str, Any]:
    return {
        "book_id": book["book_id"],
        "title": book["title"],
        "author": book.get("author"),
        "pdf_link": admin.pdf_link(book["file_path"] or "") or "#",
        "pages": book["pages"][:60],
        "page_count": len(book["pages"]),
    }


@router.get("/{concept_id}")
def concept_detail(concept_id: int):
    with db() as connection:
        concept = concept_store.get_concept(connection, concept_id)
    if concept is None:
        raise HTTPException(404, "Unknown concept")
    concept["books"] = [_book_out(book) for book in concept["books"]]
    concept["definitions"] = [
        {"book_id": d["book_id"], "title": d["title"], "page_number": d["page"], "sentence": d["sentence"],
         "pdf_link": admin.pdf_link(d["file_path"] or "") or "#"}
        for d in concept["definitions"]
    ]
    concept["figures"] = [
        {"image": page_assets.asset_url(f["file_hash"], f["image"]), "caption": f["caption"], "label": f["label"],
         "width": f["width"], "height": f["height"], "page_number": f["page_number"], "title": f["title"],
         "book_id": f["book_id"], "primary": True}
        for f in concept["figures"]
        if f["file_hash"]
    ]
    return concept


@router.get("/{a}/links/{b}")
def link_detail(a: int, b: int):
    with db() as connection:
        link = concept_store.get_link(connection, a, b)
    if link is None:
        raise HTTPException(404, "These concepts are not linked")
    link["evidence"] = [
        {"book_id": e["book_id"], "title": e["title"], "page_number": e["page"], "sentence": e["sentence"],
         "pdf_link": admin.pdf_link(e["file_path"] or "") or "#"}
        for e in link["evidence"]
    ]
    return link
