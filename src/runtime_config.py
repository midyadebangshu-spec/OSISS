"""Settings the admin app may change while the API runs, persisted across restarts.

`Settings` is frozen and read from the environment at import time. A small allowlist of fields that are read on
every request (search knobs, access mode, CORS origins, public URL) can be changed through the admin API: the new
value is set on the shared `settings` object and saved to SETTINGS_OVERRIDES_PATH, which `load_overrides` applies
at the next startup. Everything else (database URLs, model paths, ADMIN_TOKEN, ...) is shown read-only and needs a
restart with a new environment.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from config import settings


@dataclass(frozen=True)
class Field:
    """One runtime-editable setting."""

    kind: str  # "bool" | "int" | "float" | "choice" | "origins" | "url"
    group: str
    label: str
    help: str = ""
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    choices: Tuple[str, ...] = ()


FIELDS: Dict[str, Field] = {
    "rerank_enabled": Field("bool", "search", "Cross-encoder rerank", "Rerank retrieved chunks with bge-reranker."),
    "qa_rank_weight": Field(
        "float", "search", "QA weight in final ranking", "0 = rerank score only, 1 = QA score only.", 0.0, 1.0
    ),
    "retrieval_candidates": Field("int", "search", "Retrieval candidates", "Chunks fetched before rerank.", 5, 200),
    "qa_extra_candidates": Field("int", "search", "Extra QA candidates", "Chunks read by QA beyond top_k.", 0, 10),
    "min_qa_score": Field("float", "search", "Minimum QA score", "Results below this are dropped.", 0.0, 1.0),
    "hybrid_mode": Field("choice", "search", "Hybrid mode", "rrf = rank fusion, sum = additive scores.",
                         choices=("rrf", "sum")),
    "mmr_lambda": Field("float", "search", "MMR lambda", "1.0 = no diversification.", 0.0, 1.0),
    "dedupe_results": Field("bool", "search", "Drop duplicate pages / near-duplicates"),
    "exclude_exercises": Field("bool", "search", "Hide exercise / question-bank chunks"),
    "language_analyzers": Field("bool", "search", "Language-analyzed keyword fields"),
    "query_stopwords": Field("bool", "search", "Strip question words from keyword query"),
    "keyword_same_language": Field("bool", "search", "Bengali/Hindi keywords only match same-language chunks"),
    "translit_enabled": Field("bool", "search", "Romanized Bengali/Hindi queries"),
    "translit_confidence": Field(
        "float", "search", "Romanized fallback threshold", "Below this top rerank score, rewrites are tried.", 0.0, 1.0
    ),
    "translit_original_weight": Field("float", "search", "Weight of the typed romanized query", "", 0.0, 2.0),
    "rerank_batch_size": Field("int", "search", "Rerank batch size", "Pairs per reranker pass; larger is faster on a GPU.", 1, 256),
    "polling_interval_seconds": Field("int", "ingestion", "Poll interval (seconds)", "", 2, 3600),
    "search_cache_size": Field("int", "load", "Answer cache size", "Recent answers kept in memory; 0 = off.", 0, 100000),
    "search_cache_ttl_seconds": Field(
        "int", "load", "Answer cache time (seconds)", "Longest an answer is reused; book changes clear it at once.", 0, 86400
    ),
    "search_queue_limit": Field(
        "int", "load", "Search queue limit", "Searches allowed to wait; more get 503 'busy, retry'.", 0, 10000
    ),
    "search_queue_timeout_seconds": Field("int", "load", "Queue wait limit (seconds)", "", 1, 600),
    "search_rate_limit_per_minute": Field(
        "int", "load", "Searches per minute per IP", "0 = no limit. Mind campus networks sharing one address.", 0, 100000
    ),
    "api_key_rate_limit_per_minute": Field("int", "load", "Searches per minute per API key", "0 = no limit.", 0, 100000),
    "search_access": Field(
        "choice", "access", "Search access",
        "open: anyone who can reach the server; key: apps need an API key (allowed origins excepted).",
        choices=("open", "key"),
    ),
    "cors_origins": Field("origins", "access", "Allowed browser origins", "e.g. https://search.example.edu"),
    "public_base_url": Field("url", "access", "Public base URL", "Makes pdf_link absolute for external apps."),
}

# Shown read-only in the admin app; changing them needs a new environment and a restart.
RESTART_FIELDS = (
    "postgres_host", "postgres_port", "postgres_db", "elasticsearch_url", "elasticsearch_index",
    "bge_model_path", "qa_model_path", "reranker_model_path", "translit_model_path", "pdf_dir", "upload_dir",
    "upload_max_mb", "chunk_size_words", "chunk_overlap_words", "min_chunk_words", "ocr_fallback", "ocr_languages",
    "embedding_dim", "inference_device", "model_precision", "search_concurrency", "admin_allowed_ips", "serve_frontend",
)

_lock = threading.Lock()
_state: Dict[str, Any] = {}


class SettingsError(ValueError):
    """A submitted value is not allowed."""


def _origin(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path not in {"", "/"}:
        raise SettingsError(f"'{value}' is not an origin like https://search.example.edu")
    return f"{parsed.scheme}://{parsed.netloc}".lower()


def coerce(name: str, value: Any) -> Any:
    """Validate a submitted value and convert it to the stored type."""
    field = FIELDS.get(name)
    if field is None:
        raise SettingsError(f"'{name}' cannot be changed at runtime")
    if field.kind == "bool":
        if not isinstance(value, bool):
            raise SettingsError(f"{name} must be true or false")
        return value
    if field.kind in {"int", "float"}:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingsError(f"{name} must be a number")
        if field.kind == "int" and int(value) != value:
            raise SettingsError(f"{name} must be a whole number")
        number = int(value) if field.kind == "int" else float(value)
        if (field.minimum is not None and number < field.minimum) or (
            field.maximum is not None and number > field.maximum
        ):
            raise SettingsError(f"{name} must be between {field.minimum} and {field.maximum}")
        return number
    if field.kind == "choice":
        if value not in field.choices:
            raise SettingsError(f"{name} must be one of {', '.join(field.choices)}")
        return value
    if field.kind == "origins":
        items = value if isinstance(value, list) else str(value or "").split(",")
        return ",".join(dict.fromkeys(_origin(item) for item in items if str(item).strip()))
    if field.kind == "url":
        text = str(value or "").strip().rstrip("/")
        if text and (urlparse(text).scheme not in {"http", "https"} or not urlparse(text).netloc):
            raise SettingsError(f"{name} must be an http(s) URL")
        return text
    raise SettingsError(f"unsupported field {name}")


def cors_origin_list() -> List[str]:
    return [item for item in settings.cors_origins.split(",") if item]


class _FileStore:
    """STATE_BACKEND=local: one JSON file, written atomically."""

    def read(self) -> Tuple[Dict[str, Any], Optional[int]]:
        try:
            with open(settings.settings_overrides_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return (data if isinstance(data, dict) else {}), None
        except FileNotFoundError:
            return {}, None

    def modify(self, change: Callable[[Dict[str, Any]], None]) -> Dict[str, Any]:
        data, _ = self.read()
        change(data)
        path = settings.settings_overrides_path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
        os.replace(tmp, path)
        return data


class _PostgresStore:
    """STATE_BACKEND=postgres: the `server_state` row shared by every API copy.

    `modify` locks the row, so two admins saving at once through different copies never lose a change. The first
    use imports an existing settings_overrides.json, so switching an install over keeps its settings.
    """

    def _ensure_row(self, cursor) -> None:
        cursor.execute("SELECT 1 FROM server_state WHERE id = 1")
        if cursor.fetchone():
            return
        imported, _ = _FileStore().read()
        cursor.execute(
            "INSERT INTO server_state (id, data, version) VALUES (1, %s, 1) ON CONFLICT (id) DO NOTHING",
            (json.dumps(imported),),
        )

    def read(self) -> Tuple[Dict[str, Any], Optional[int]]:
        from db_pool import db  # pylint: disable=import-outside-toplevel

        with db() as connection:
            with connection:
                with connection.cursor() as cursor:
                    self._ensure_row(cursor)
                    cursor.execute("SELECT data, version FROM server_state WHERE id = 1")
                    data, version = cursor.fetchone()
        return (data or {}), version

    def version(self) -> Optional[int]:
        from db_pool import db  # pylint: disable=import-outside-toplevel

        with db() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT version FROM server_state WHERE id = 1")
                row = cursor.fetchone()
        return row[0] if row else None

    def modify(self, change: Callable[[Dict[str, Any]], None]) -> Dict[str, Any]:
        from db_pool import db  # pylint: disable=import-outside-toplevel

        with db() as connection:
            with connection:
                with connection.cursor() as cursor:
                    self._ensure_row(cursor)
                    cursor.execute("SELECT data FROM server_state WHERE id = 1 FOR UPDATE")
                    data = cursor.fetchone()[0] or {}
                    change(data)
                    cursor.execute(
                        "UPDATE server_state SET data = %s, version = version + 1, updated_at = NOW() "
                        "WHERE id = 1 RETURNING version",
                        (json.dumps(data),),
                    )
                    global _applied_version  # pylint: disable=global-statement
                    _applied_version = cursor.fetchone()[0]
        return data


_applied_version: Optional[int] = None


def _store():
    return _PostgresStore() if settings.state_backend == "postgres" else _FileStore()


def _apply(data: Dict[str, Any]) -> Tuple[List[str], Dict[str, Any]]:
    """Set saved values on `settings` and the state dict; returns (warnings, values that changed)."""
    warnings: List[str] = []
    changed: Dict[str, Any] = {}
    for name, value in (data.get("settings") or {}).items():
        try:
            coerced = coerce(name, value)
        except SettingsError as exc:
            warnings.append(f"Ignored saved setting: {exc}")
            continue
        if getattr(settings, name) != coerced:
            changed[name] = coerced
        object.__setattr__(settings, name, coerced)
    _state.clear()
    _state.update(data.get("state") or {})
    return warnings, changed


def load_overrides() -> List[str]:
    """Apply saved overrides to `settings` (API startup). Returns warnings for skipped entries."""
    global _applied_version  # pylint: disable=global-statement
    with _lock:
        data, version = _store().read()
        warnings, _ = _apply(data)
        _applied_version = version
    return warnings


def sync_once() -> Dict[str, Any]:
    """Pick up settings another API copy saved (STATE_BACKEND=postgres). Returns the values that changed."""
    global _applied_version  # pylint: disable=global-statement
    if settings.state_backend != "postgres":
        return {}
    store = _PostgresStore()
    if store.version() == _applied_version:
        return {}
    with _lock:
        data, version = store.read()
        _, changed = _apply(data)
        _applied_version = version
    _after_change(changed)
    for listener in _listeners:
        listener(changed)
    return changed


_listeners: List[Callable[[Dict[str, Any]], None]] = []


def on_sync(listener: Callable[[Dict[str, Any]], None]) -> None:
    """Call `listener(changed_values)` after settings or state saved elsewhere were applied here."""
    _listeners.append(listener)


def start_sync(stop: threading.Event) -> Optional[threading.Thread]:
    """Background thread polling for settings changed through other copies (postgres backend only)."""
    if settings.state_backend != "postgres":
        return None

    def loop() -> None:
        while not stop.wait(settings.settings_sync_seconds):
            try:
                changed = sync_once()
                if changed:
                    print(f"[OSISS] Settings changed on another copy: {', '.join(sorted(changed))}")
            except Exception as exc:  # pylint: disable=broad-except
                print(f"[OSISS] Settings sync failed: {exc}")

    thread = threading.Thread(target=loop, name="osiss-settings-sync", daemon=True)
    thread.start()
    return thread


def update(values: Dict[str, Any]) -> Dict[str, Any]:
    """Validate all values first, then apply and persist them together. Returns the applied values."""
    coerced = {name: coerce(name, value) for name, value in values.items()}

    def change(data: Dict[str, Any]) -> None:
        saved = dict(data.get("settings") or {})
        saved.update(coerced)
        data["settings"] = saved

    with _lock:
        # Apply the whole stored document: it may also hold changes another copy saved since our last sync.
        _, changed = _apply(_store().modify(change))
    _after_change({**changed, **coerced})
    return coerced


def _after_change(changed: Dict[str, Any]) -> None:
    """Reset the cached reranker / transliterator when their toggle changes.

    They cache `None` while disabled, so turning one on must clear the cache too; turning one off frees the model.
    """
    if "rerank_enabled" not in changed and "translit_enabled" not in changed:
        return
    try:
        import search  # pylint: disable=import-outside-toplevel
        import translit  # pylint: disable=import-outside-toplevel
    except Exception:  # pylint: disable=broad-except
        return
    if "rerank_enabled" in changed:
        search.get_reranker.cache_clear()
    if "translit_enabled" in changed:
        translit.get_translator.cache_clear()


def get_state(key: str, default: Any = None) -> Any:
    with _lock:
        return _state.get(key, default)


def set_state(key: str, value: Any) -> None:
    """Persist a piece of server state (e.g. whether the ingester polls) next to the settings."""

    def change(data: Dict[str, Any]) -> None:
        state = dict(data.get("state") or {})
        state[key] = value
        data["state"] = state

    with _lock:
        _apply(_store().modify(change))


def describe() -> Dict[str, Any]:
    """Editable fields with current values and metadata, plus read-only restart-only values."""
    editable = []
    for name, field in FIELDS.items():
        value = getattr(settings, name)
        if field.kind == "origins":
            value = cors_origin_list()
        editable.append(
            {
                "name": name,
                "env": name.upper(),
                "kind": field.kind,
                "group": field.group,
                "label": field.label,
                "help": field.help,
                "min": field.minimum,
                "max": field.maximum,
                "choices": list(field.choices),
                "value": value,
            }
        )
    readonly = [{"name": name, "env": name.upper(), "value": getattr(settings, name)} for name in RESTART_FIELDS]
    return {"editable": editable, "readonly": readonly}
