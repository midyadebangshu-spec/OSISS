"""Context Explorer storage: write a built concept graph to PostgreSQL and answer the explorer's lookups.

The graph is replaced as a whole (one transaction), so readers see either the old or the new one. Lookups return
plain dicts for `concept_api`; concept search runs in memory over an alias index cached for a minute.
"""

from __future__ import annotations

import json
import threading
import time
import unicodedata
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from psycopg2.extras import execute_values

from concepts import Graph, normalize, singular, words_of

INDEX_TTL_SECONDS = 60


# ---------------------------------------------------------------------------------------------- writing


def write_graph(connection, graph: Graph, figures: Sequence[Tuple[str, int, int, Dict]] = ()) -> Dict[str, int]:
    """Replace the stored graph. `figures`: (concept key, book_id, page, figure dict from page_assets)."""
    mention_books: Dict[str, set] = {}
    definition_counts: Dict[str, int] = {}
    mention_counts: Dict[str, int] = {}
    for mention in graph.mentions:
        mention_books.setdefault(mention.key, set()).add(mention.book_id)
        mention_counts[mention.key] = mention_counts.get(mention.key, 0) + 1
        definition_counts[mention.key] = definition_counts.get(mention.key, 0) + int(mention.is_definition)

    with connection:
        with connection.cursor() as cursor:
            cursor.execute("TRUNCATE concept_links, concept_mentions, concept_figures, concepts RESTART IDENTITY")
            rows = [
                (
                    key,
                    concept.label,
                    json.dumps(sorted(concept.aliases), ensure_ascii=False),
                    len(mention_books.get(key, ())),
                    mention_counts.get(key, 0),
                    definition_counts.get(key, 0),
                )
                for key, concept in graph.concepts.items()
            ]
            ids = execute_values(
                cursor,
                "INSERT INTO concepts (key, label, aliases, book_count, mention_count, definition_count) VALUES %s "
                "RETURNING id, key",
                rows,
                fetch=True,
                page_size=1000,
            )
            id_of = {key: concept_id for concept_id, key in ids}
            execute_values(
                cursor,
                "INSERT INTO concept_mentions (concept_id, book_id, chunk_id, page_number, is_definition, sentence) VALUES %s",
                [
                    (id_of[m.key], m.book_id, m.chunk_id, m.page, m.is_definition, m.sentence)
                    for m in graph.mentions
                    if m.key in id_of
                ],
                page_size=5000,
            )
            execute_values(
                cursor,
                "INSERT INTO concept_links (source_id, target_id, weight, npmi, sentence_count, chunk_count, book_count, "
                "relation, relation_source_id, order_hint, evidence) VALUES %s",
                [
                    (
                        id_of[link.a],
                        id_of[link.b],
                        link.weight,
                        link.npmi,
                        link.sentence_count,
                        link.chunk_count,
                        len(link.books),
                        link.relation,
                        id_of.get(link.relation_source) if link.relation_source else None,
                        link.order,
                        json.dumps(link.evidence, ensure_ascii=False),
                    )
                    for link in graph.links
                    if link.a in id_of and link.b in id_of
                ],
                page_size=2000,
            )
            execute_values(
                cursor,
                "INSERT INTO concept_figures (concept_id, book_id, page_number, image, caption, label, width, height) VALUES %s",
                [
                    (id_of[key], book_id, page, fig["image"], fig.get("caption"), fig.get("label"), fig.get("width"), fig.get("height"))
                    for key, book_id, page, fig in figures
                    if key in id_of
                ],
                page_size=2000,
            )
    invalidate_index()
    return {"concepts": len(id_of), "mentions": len(graph.mentions), "links": len(graph.links), "figures": len(figures)}


# ---------------------------------------------------------------------------------------------- search


_index_lock = threading.Lock()
_index: Dict = {"loaded": 0.0, "items": None}


def invalidate_index() -> None:
    with _index_lock:
        _index["loaded"] = 0.0


def load_index(connection) -> List[Dict]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, label, aliases, book_count, mention_count, definition_count FROM concepts")
        rows = cursor.fetchall()
    return [
        {"id": r[0], "label": r[1], "aliases": r[2], "book_count": r[3], "mention_count": r[4], "definition_count": r[5]}
        for r in rows
    ]


def cached_index(connect: Callable) -> List[Dict]:
    with _index_lock:
        if _index["items"] is not None and time.monotonic() - _index["loaded"] < INDEX_TTL_SECONDS:
            return _index["items"]
    with connect() as connection:
        items = load_index(connection)
    with _index_lock:
        _index.update(items=items, loaded=time.monotonic())
    return items


QUESTION_WORDS = frozenset(
    "what is are the a an of define definition explain meaning describe about কী কি কাকে বলে সংজ্ঞা লেখ "
    "क्या है हैं किसे कहते परिभाषा".split()
)


def _query_forms(query: str) -> List[str]:
    text = normalize(query)
    words = [word for word in words_of(text) if word not in QUESTION_WORDS]
    forms = [" ".join(words), text]
    if words:
        forms.append(" ".join(words[:-1] + [singular(words[-1])]))
    return [form for form in dict.fromkeys(forms) if form]


def _word_match(form: str, alias: str) -> bool:
    query_words, alias_words = set(words_of(form)), set(words_of(alias))
    if not query_words or not alias_words or len(form) < 3:
        return False
    return query_words <= alias_words or alias_words <= query_words


def rank_matches(query: str, items: Sequence[Dict], extra_forms: Sequence[str] = (), limit: int = 12) -> List[Dict]:
    """Concepts whose names match a query: exact > prefix > word match > substring, then by book coverage."""
    forms = _query_forms(query) + [normalize(form) for form in extra_forms if form]
    if not forms:
        return []
    scored = []
    for item in items:
        best = 0.0
        for alias in item["aliases"] + [normalize(item["label"])]:
            alias_n = unicodedata.normalize("NFC", alias)
            for form in forms:
                if alias_n == form:
                    score = 100.0
                elif alias_n.startswith(form) or form.startswith(alias_n) and len(alias_n) >= 4:
                    score = 60.0 - abs(len(alias_n) - len(form)) * 0.5
                elif _word_match(form, alias_n):
                    # Every query word in the name ("stack" -> "stack frame"), or the whole name in the query.
                    score = 30.0 + 5 * len(set(words_of(form)) & set(words_of(alias_n))) - abs(len(alias_n.split()) - len(form.split()))
                elif len(form) >= 4 and form in alias_n:
                    score = 20.0
                else:
                    continue
                best = max(best, score)
        if best:
            scored.append((best + 2.0 * item["book_count"] + 0.5 * min(item["definition_count"], 4), item))
    scored.sort(key=lambda pair: -pair[0])
    return [item for _, item in scored[:limit]]


def top_concepts(items: Sequence[Dict], limit: int = 60) -> List[Dict]:
    """For browsing: concepts defined repeatedly and covered by several books. Ranking by coverage alone put
    everyday words first ("side", "green", "round"); definitions are what make a term a concept."""

    def score(item: Dict) -> float:
        multiword = 2 if len(item["label"].split()) > 1 else 0
        return 2 * min(item["definition_count"], 6) + min(item["book_count"], 8) + multiword

    candidates = [i for i in items if i["definition_count"] >= 2 and i["book_count"] >= 2] or list(items)
    return sorted(candidates, key=lambda i: (-score(i), i["label"]))[:limit]


# ---------------------------------------------------------------------------------------------- one concept


def get_concept(connection, concept_id: int) -> Optional[Dict]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, label, aliases, book_count, mention_count, definition_count FROM concepts WHERE id = %s",
            (concept_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        concept = {"id": row[0], "label": row[1], "aliases": row[2], "book_count": row[3], "mention_count": row[4],
                   "definition_count": row[5]}
        cursor.execute(
            "SELECT m.book_id, b.title, b.author, b.file_path, b.file_hash, m.page_number, m.is_definition, m.sentence "
            "FROM concept_mentions m JOIN books b ON b.id = m.book_id WHERE m.concept_id = %s "
            "ORDER BY m.book_id, m.page_number",
            (concept_id,),
        )
        mentions = cursor.fetchall()
        cursor.execute(
            "SELECT l.source_id, l.target_id, l.weight, l.sentence_count, l.book_count, l.relation, l.relation_source_id, "
            "l.order_hint, c.id, c.label, c.book_count, c.aliases FROM concept_links l "
            "JOIN concepts c ON c.id = CASE WHEN l.source_id = %s THEN l.target_id ELSE l.source_id END "
            "WHERE l.source_id = %s OR l.target_id = %s ORDER BY l.weight DESC LIMIT 24",
            (concept_id, concept_id, concept_id),
        )
        links = cursor.fetchall()
        neighbour_ids = [row[8] for row in links]
        cross = []
        if neighbour_ids:
            cursor.execute(
                "SELECT source_id, target_id, weight FROM concept_links WHERE source_id = ANY(%s) AND target_id = ANY(%s)",
                (neighbour_ids, neighbour_ids),
            )
            cross = cursor.fetchall()
        cursor.execute(
            "SELECT f.book_id, b.title, b.file_hash, f.page_number, f.image, f.caption, f.label, f.width, f.height "
            "FROM concept_figures f JOIN books b ON b.id = f.book_id WHERE f.concept_id = %s "
            "ORDER BY (f.caption IS NULL), f.book_id, f.page_number LIMIT 12",
            (concept_id,),
        )
        figures = cursor.fetchall()

    books: Dict[int, Dict] = {}
    definitions = []
    for book_id, title, author, file_path, file_hash, page, is_definition, sentence in mentions:
        book = books.setdefault(
            book_id, {"book_id": book_id, "title": title, "author": author, "file_path": file_path, "pages": []}
        )
        if page not in book["pages"]:
            book["pages"].append(page)
        if is_definition and sum(1 for d in definitions if d["book_id"] == book_id) < 2:
            definitions.append({"book_id": book_id, "title": title, "file_path": file_path, "page": page, "sentence": sentence})
    concept["books"] = sorted(books.values(), key=lambda b: -len(b["pages"]))
    concept["definitions"] = definitions
    concept["neighbours"] = [
        {
            "id": other_id,
            "label": other_label,
            "aliases": other_aliases,
            "book_count": other_books,
            "weight": round(weight, 3),
            "sentence_count": sentence_count,
            "shared_books": shared_books,
            "relation": relation,
            # "out": this concept is the relation's source ("stack is a type of data structure").
            "direction": None if relation is None else ("out" if relation_source == concept_id else "in"),
            # > 0: most books reach this concept before the neighbour.
            "order": order if source == concept_id else -order,
        }
        for source, _target, weight, sentence_count, shared_books, relation, relation_source, order, other_id,
        other_label, other_books, other_aliases in links
    ]
    concept["neighbour_links"] = [{"a": a, "b": b, "weight": round(w, 3)} for a, b, w in cross]
    concept["figures"] = [
        {"book_id": book_id, "title": title, "file_hash": file_hash, "page_number": page, "image": image,
         "caption": caption, "label": label, "width": width, "height": height}
        for book_id, title, file_hash, page, image, caption, label, width, height in figures
    ]
    return concept


def get_link(connection, a: int, b: int) -> Optional[Dict]:
    source, target = min(a, b), max(a, b)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT weight, sentence_count, book_count, relation, relation_source_id, order_hint, evidence "
            "FROM concept_links WHERE (source_id = %s AND target_id = %s) OR (source_id = %s AND target_id = %s)",
            (source, target, target, source),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        cursor.execute("SELECT id, label FROM concepts WHERE id IN (%s, %s)", (a, b))
        labels = dict(cursor.fetchall())
        book_ids = sorted({item["book_id"] for item in row[6]})
        titles = {}
        if book_ids:
            cursor.execute("SELECT id, title, file_path FROM books WHERE id = ANY(%s)", (book_ids,))
            titles = {book_id: (title, path) for book_id, title, path in cursor.fetchall()}
    return {
        "a": {"id": a, "label": labels.get(a)},
        "b": {"id": b, "label": labels.get(b)},
        "weight": row[0],
        "sentence_count": row[1],
        "book_count": row[2],
        "relation": row[3],
        "relation_source_id": row[4],
        "evidence": [
            {**item, "title": titles.get(item["book_id"], ("", ""))[0], "file_path": titles.get(item["book_id"], ("", ""))[1]}
            for item in row[6]
        ],
    }
