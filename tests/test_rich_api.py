"""/api/search/rich and /data/assets without services: fake pipeline, synthetic PDF, real page-asset cache."""

import os
import sys
from contextlib import asynccontextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("torch")

from fastapi.testclient import TestClient

import api_server
import load_control
import page_assets
from config import settings
from test_page_assets import make_pdf

FILE_HASH = "b" * 64


@asynccontextmanager
async def _no_lifespan(_app):
    yield


@pytest.fixture
def client(tmp_path, monkeypatch):
    saved = {key: getattr(settings, key) for key in ("asset_dir", "page_assets", "search_access", "public_base_url")}
    for key, value in {"asset_dir": str(tmp_path / "assets"), "page_assets": True, "search_access": "", "public_base_url": ""}.items():
        object.__setattr__(settings, key, value)
    pdf_path = str(tmp_path / "book.pdf")
    make_pdf(pdf_path)
    calls = []

    monkeypatch.setattr(api_server.app.router, "lifespan_context", _no_lifespan)
    monkeypatch.setattr(load_control, "cache", load_control.SearchCache())
    monkeypatch.setattr(load_control, "limiter", load_control.RateLimiter())
    monkeypatch.setattr(load_control, "metrics", load_control.Metrics())
    monkeypatch.setattr(load_control, "_gate", None)

    def fake_search(query, top_k, timings=None, book_id=None):
        calls.append(top_k if book_id is None else (top_k, book_id))
        return {"query": query, "results": [{
            "quote": "The angle of incidence is always equal to the angle of reflection for a plane mirror.",
            "matched_paragraph": "Light travels in straight lines. The angle of incidence is always equal to the angle of "
                                 "reflection for a plane mirror (Fig. 2.1).",
            "chunk_preview": "", "qa_score": 0.8, "qa_answer": "equal",
            "source": {"book_title": "Optics", "page_number": 1, "file_path": pdf_path},
        }]}

    monkeypatch.setattr(api_server, "search_and_extract", fake_search)
    monkeypatch.setattr(api_server, "book_hashes", lambda paths: {pdf_path: FILE_HASH})
    with TestClient(api_server.app) as test_client:
        test_client.calls = calls
        yield test_client
    for key, value in saved.items():
        object.__setattr__(settings, key, value)


def test_rich_search_builds_pages_on_demand_and_serves_them(client):
    response = client.post("/api/search/rich", json={"query": "diagram of reflection from a mirror"})
    assert response.status_code == 200
    body = response.json()
    assert body["layout"] == {"kind": "visual", "hero": 0, "intents": ["visual"]}
    result = body["results"][0]
    assert result["book_title"] == "Optics" and result["page_number"] == 1
    assert result["figures"][0]["label"] == "2.1" and result["figures"][0]["primary"]
    assert result["page"]["highlights"], "the quote is highlighted on the page image"

    image = client.get(result["figures"][0]["image"])
    assert image.status_code == 200 and image.headers["content-type"] == "image/webp"
    assert client.get(result["page"]["image"]).status_code == 200


def test_plain_search_contract_is_unchanged(client):
    body = client.post("/api/search", json={"query": "mirror"}).json()
    assert set(body["results"][0]) == {
        "exact_quote", "book_title", "author", "department", "page_number", "pdf_link", "paragraph_text", "answer_text"}


def test_rich_search_without_page_assets(client):
    object.__setattr__(settings, "page_assets", False)
    result = client.post("/api/search/rich", json={"query": "mirror"}).json()["results"][0]
    assert result["page"] is None and result["figures"] == [] and result["kind"] == "text"


@pytest.mark.parametrize(
    "path",
    [
        f"/data/assets/{FILE_HASH}/p1.json",          # page data stays private
        f"/data/assets/{FILE_HASH}/..%2Fsecret.webp",
        "/data/assets/not-a-hash/p1.webp",
        f"/data/assets/{FILE_HASH}/p999.webp",       # missing
    ],
)
def test_asset_route_only_serves_cached_images(client, path):
    client.post("/api/search/rich", json={"query": "mirror"})
    assert client.get(path).status_code == 404


def test_top_k_is_optional_and_passed_through(client):
    client.post("/api/search/rich", json={"query": "mirror"})
    client.post("/api/search", json={"query": "lens", "top_k": 4})
    assert client.calls == [None, 4]  # no top_k: the pipeline applies the relative score threshold
    assert client.post("/api/search", json={"query": "x", "top_k": 11}).status_code == 422


def test_book_scoped_search_is_passed_through_and_cached_separately(client):
    client.post("/api/search/rich", json={"query": "mirror", "book_id": 3})
    client.post("/api/search/rich", json={"query": "mirror"})
    client.post("/api/search/rich", json={"query": "mirror", "book_id": 3})  # answered from the cache
    assert client.calls == [(None, 3), None]
    assert client.post("/api/search/rich", json={"query": "mirror", "book_id": 0}).status_code == 422
