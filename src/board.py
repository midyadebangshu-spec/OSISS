"""Research board: claims, evidence, concepts and notes of a project laid out on a canvas, joined by typed edges.

The board never writes text of its own. A claim is the scholar's sentence; evidence nodes show a saved item's quote with
its source; "supports" / "contradicts" / "explains" / "relates" edges record how things bear on each other. Claims with no
supporting evidence are reported, and `outline_body` turns the board into a draft skeleton (claims as headings, their
evidence as cited block quotations) for the writing studio.

Pure helpers first, then the DB functions (an open psycopg2 connection and the owner's id; nothing commits).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

KINDS = ("claim", "evidence", "concept", "note")
RELATIONS = ("supports", "contradicts", "explains", "relates")
MAX_NODES = 300
MAX_EDGES = 1000
LIMIT = 5000  # coordinates stay within +-LIMIT canvas units
CLAIM_MAX = 1000
GRID_COLUMNS = 4
GRID_STEP = (260.0, 170.0)
NEIGHBOURS = 6  # concepts added next to a seeded concept

RELATION_PHRASES = {
    "is_a": "is a type of", "has_part": "consists of", "used_for": "is used for", "causes": "causes",
    "contrast": "contrasts with",
}


class BoardError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# --- pure helpers -------------------------------------------------------------------------------------------------------


def clamp_position(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise BoardError("Positions are numbers.") from exc
    if number != number or abs(number) > LIMIT:  # NaN or out of range
        raise BoardError(f"Positions stay within {LIMIT} of the centre.")
    return round(number, 1)


def grid_position(index: int) -> Tuple[float, float]:
    """Where the n-th automatically placed node goes (a grid, left to right, then down)."""
    return (index % GRID_COLUMNS) * GRID_STEP[0], (index // GRID_COLUMNS) * GRID_STEP[1]


def ring_positions(count: int, cx: float, cy: float, radius: float = 330.0) -> List[Tuple[float, float]]:
    """Positions for `count` nodes evenly spaced on a circle around (cx, cy), the first straight above."""
    import math

    return [
        (round(cx + radius * math.sin(2 * math.pi * i / count), 1), round(cy - radius * math.cos(2 * math.pi * i / count), 1))
        for i in range(count)
    ] if count else []


def unsupported_claims(nodes: Sequence[Dict[str, Any]], edges: Sequence[Dict[str, Any]]) -> List[int]:
    """Ids of claims with no evidence node joined by a "supports" edge (in either direction)."""
    kinds = {node["id"]: node["kind"] for node in nodes}
    supported = set()
    for edge in edges:
        if edge["relation"] != "supports":
            continue
        for claim, other in ((edge["source_id"], edge["target_id"]), (edge["target_id"], edge["source_id"])):
            if kinds.get(claim) == "claim" and kinds.get(other) == "evidence":
                supported.add(claim)
    return [node["id"] for node in nodes if node["kind"] == "claim" and node["id"] not in supported]


def outline_body(nodes: Sequence[Dict[str, Any]], edges: Sequence[Dict[str, Any]]) -> str:
    """A draft skeleton from the board: each claim a heading (top to bottom, left to right), followed by its supporting
    evidence as cited block quotations, counter-evidence, and the scholar's own explaining notes."""
    by_id = {node["id"]: node for node in nodes}
    used_evidence = set()

    def linked(claim_id: int, relation: str, kind: str) -> List[Dict[str, Any]]:
        found = []
        for edge in edges:
            if edge["relation"] != relation:
                continue
            for mine, other in ((edge["source_id"], edge["target_id"]), (edge["target_id"], edge["source_id"])):
                node = by_id.get(other)
                if mine == claim_id and node and node["kind"] == kind and node not in found:
                    found.append(node)
        return sorted(found, key=lambda n: (n["y"], n["x"], n["id"]))

    def quote(node: Dict[str, Any]) -> Optional[str]:
        item = node.get("evidence") or {}
        text = " ".join((item.get("quote") or "").split())
        if not text or not item.get("id"):
            return None
        used_evidence.add(node["id"])
        return f"> {text} [@e{item['id']}]"

    parts: List[str] = []
    claims = sorted((n for n in nodes if n["kind"] == "claim"), key=lambda n: (n["y"], n["x"], n["id"]))
    for claim in claims:
        parts.append(f"## {' '.join(claim['text'].split())}")
        quotes = [q for q in map(quote, linked(claim["id"], "supports", "evidence")) if q]
        parts.extend(quotes or ["TODO: add supporting evidence."])
        against = [q for q in map(quote, linked(claim["id"], "contradicts", "evidence")) if q]
        if against:
            parts.append("Counter-evidence:")
            parts.extend(against)
        for note in linked(claim["id"], "explains", "note"):
            body = " ".join((note.get("note") or {}).get("body", "").split())
            if body:
                parts.append(body)
    rest = [q for q in (quote(n) for n in sorted((n for n in nodes if n["kind"] == "evidence" and n["id"] not in used_evidence), key=lambda n: (n["y"], n["x"], n["id"]))) if q]
    if rest:
        parts.append("## Other evidence")
        parts.extend(rest)
    return "\n\n".join(parts) + ("\n" if parts else "")


# --- DB: access ---------------------------------------------------------------------------------------------------------


def _own_project(connection, owner_id: int, project_id: int) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT title FROM projects WHERE id = %s AND owner_id = %s", (project_id, owner_id))
        row = cursor.fetchone()
    if row is None:
        raise BoardError("Unknown project.", 404)


NODE_SQL = (
    "SELECT n.id, n.kind, n.text, n.x, n.y, n.evidence_id, n.note_id, n.concept_key, "
    "e.quote, e.book_id, e.book_title, e.book_author, e.page_number, e.kind, "
    "t.title, t.body FROM board_nodes n "
    "LEFT JOIN evidence_items e ON e.id = n.evidence_id LEFT JOIN notes t ON t.id = n.note_id"
)


def _node(row) -> Dict[str, Any]:
    node: Dict[str, Any] = {"id": row[0], "kind": row[1], "text": row[2], "x": row[3], "y": row[4]}
    if row[5] is not None:
        node["evidence"] = {
            "id": row[5], "quote": row[8], "book_id": row[9], "book_title": row[10], "book_author": row[11],
            "page_number": row[12], "kind": row[13],
        }
    if row[6] is not None:
        node["note"] = {"id": row[6], "title": row[14], "body": row[15]}
    if row[7] is not None:
        node["concept_key"] = row[7]
    return node


def _edge(row) -> Dict[str, Any]:
    return {"id": row[0], "source_id": row[1], "target_id": row[2], "relation": row[3], "note": row[4]}


def get_board(connection, owner_id: int, project_id: int) -> Dict[str, Any]:
    import workspace  # pylint: disable=import-outside-toplevel

    _own_project(connection, owner_id, project_id)
    with connection.cursor() as cursor:
        cursor.execute(f"{NODE_SQL} WHERE n.project_id = %s ORDER BY n.id", (project_id,))
        nodes = [_node(row) for row in cursor.fetchall()]
        cursor.execute(
            "SELECT id, source_id, target_id, relation, note FROM board_edges WHERE project_id = %s ORDER BY id", (project_id,)
        )
        edges = [_edge(row) for row in cursor.fetchall()]
    workspace.add_printed_pages(connection, [node["evidence"] for node in nodes if "evidence" in node])
    return {"project_id": project_id, "nodes": nodes, "edges": edges, "unsupported": unsupported_claims(nodes, edges)}


def _count(connection, table: str, project_id: int) -> int:
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE project_id = %s", (project_id,))
        return cursor.fetchone()[0]


def _one(connection, project_id: int, node_id: int) -> Dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(f"{NODE_SQL} WHERE n.id = %s AND n.project_id = %s", (node_id, project_id))
        row = cursor.fetchone()
    if row is None:
        raise BoardError("Unknown node.", 404)
    return _node(row)


# --- DB: nodes -----------------------------------------------------------------------------------------------------------


def add_node(
    connection, owner_id: int, project_id: int, kind: str, text: str = "", evidence_id: Optional[int] = None,
    note_id: Optional[int] = None, concept_key: Optional[str] = None, x: Optional[float] = None, y: Optional[float] = None,
) -> Dict[str, Any]:
    _own_project(connection, owner_id, project_id)
    if kind not in KINDS:
        raise BoardError(f"kind must be one of {', '.join(KINDS)}.")
    count = _count(connection, "board_nodes", project_id)
    if count >= MAX_NODES:
        raise BoardError(f"A board holds at most {MAX_NODES} nodes.", 409)
    text = " ".join((text or "").split())
    concept = " ".join((concept_key or "").split())[:200] or None
    if kind == "claim":
        if not text:
            raise BoardError("A claim needs its text.")
        text = text[:CLAIM_MAX]
        evidence_id = note_id = concept = None
    elif kind == "evidence":
        if evidence_id is None:
            raise BoardError("An evidence node needs evidence_id.")
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM evidence_items WHERE id = %s AND owner_id = %s", (evidence_id, owner_id))
            if not cursor.fetchone():
                raise BoardError("Unknown evidence item.", 404)
            cursor.execute("SELECT 1 FROM board_nodes WHERE project_id = %s AND evidence_id = %s", (project_id, evidence_id))
            if cursor.fetchone():
                raise BoardError("That item is already on the board.", 409)
        text, note_id, concept = "", None, None
    elif kind == "note":
        if note_id is None:
            raise BoardError("A note node needs note_id.")
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM notes WHERE id = %s AND owner_id = %s", (note_id, owner_id))
            if not cursor.fetchone():
                raise BoardError("Unknown note.", 404)
            cursor.execute("SELECT 1 FROM board_nodes WHERE project_id = %s AND note_id = %s", (project_id, note_id))
            if cursor.fetchone():
                raise BoardError("That note is already on the board.", 409)
        text, evidence_id, concept = "", None, None
    else:  # concept
        if not concept:
            raise BoardError("A concept node needs concept_key.")
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM board_nodes WHERE project_id = %s AND kind = 'concept' AND concept_key = %s", (project_id, concept))
            if cursor.fetchone():
                raise BoardError("That concept is already on the board.", 409)
        text, evidence_id, note_id = "", None, None
    px, py = (grid_position(count) if x is None or y is None else (clamp_position(x), clamp_position(y)))
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO board_nodes (project_id, kind, text, evidence_id, note_id, concept_key, x, y) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (project_id, kind, text, evidence_id, note_id, concept, px, py),
        )
        node_id = cursor.fetchone()[0]
        cursor.execute("UPDATE projects SET updated_at = NOW() WHERE id = %s", (project_id,))
    return _one(connection, project_id, node_id)


def update_node(
    connection, owner_id: int, project_id: int, node_id: int, text: Optional[str] = None, x: Optional[float] = None,
    y: Optional[float] = None,
) -> Dict[str, Any]:
    _own_project(connection, owner_id, project_id)
    current = _one(connection, project_id, node_id)
    new_text = current["text"]
    if text is not None:
        if current["kind"] != "claim":
            raise BoardError("Only a claim's text can be edited.")
        new_text = " ".join(text.split())[:CLAIM_MAX]
        if not new_text:
            raise BoardError("A claim needs its text.")
    new_x = clamp_position(x) if x is not None else current["x"]
    new_y = clamp_position(y) if y is not None else current["y"]
    with connection.cursor() as cursor:
        cursor.execute("UPDATE board_nodes SET text = %s, x = %s, y = %s WHERE id = %s AND project_id = %s",
                       (new_text, new_x, new_y, node_id, project_id))
    return _one(connection, project_id, node_id)


def delete_node(connection, owner_id: int, project_id: int, node_id: int) -> bool:
    """Removes the node and its edges; the saved item or note it showed is untouched."""
    _own_project(connection, owner_id, project_id)
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM board_nodes WHERE id = %s AND project_id = %s", (node_id, project_id))
        return cursor.rowcount > 0


# --- DB: edges ------------------------------------------------------------------------------------------------------------


def add_edge(
    connection, owner_id: int, project_id: int, source_id: int, target_id: int, relation: str, note: str = ""
) -> Dict[str, Any]:
    _own_project(connection, owner_id, project_id)
    if relation not in RELATIONS:
        raise BoardError(f"relation must be one of {', '.join(RELATIONS)}.")
    if source_id == target_id:
        raise BoardError("A node cannot be joined to itself.")
    if _count(connection, "board_edges", project_id) >= MAX_EDGES:
        raise BoardError(f"A board holds at most {MAX_EDGES} links.", 409)
    with connection.cursor() as cursor:
        cursor.execute("SELECT id FROM board_nodes WHERE project_id = %s AND id = ANY(%s)", (project_id, [source_id, target_id]))
        if len(cursor.fetchall()) != 2:
            raise BoardError("Both ends must be nodes on this board.", 404)
        cursor.execute(
            "INSERT INTO board_edges (project_id, source_id, target_id, relation, note) VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (source_id, target_id, relation) DO UPDATE SET note = EXCLUDED.note "
            "RETURNING id, source_id, target_id, relation, note",
            (project_id, source_id, target_id, relation, " ".join(note.split())[:300]),
        )
        return _edge(cursor.fetchone())


def delete_edge(connection, owner_id: int, project_id: int, edge_id: int) -> bool:
    _own_project(connection, owner_id, project_id)
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM board_edges WHERE id = %s AND project_id = %s", (edge_id, project_id))
        return cursor.rowcount > 0


# --- DB: seeding from the concept graph and the outline ------------------------------------------------------------------------


def seed_concept(connection, owner_id: int, project_id: int, concept_id: int, neighbours: int = NEIGHBOURS) -> Dict[str, Any]:
    """Put a concept and its strongest neighbours on the board, joined as the concept graph relates them ("relates"
    edges carrying the relation as a note). Concepts already on the board are reused, not duplicated."""
    _own_project(connection, owner_id, project_id)
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, label FROM concepts WHERE id = %s", (concept_id,))
        centre = cursor.fetchone()
        if centre is None:
            raise BoardError("Unknown concept.", 404)
        cursor.execute(
            "SELECT c.id, c.label, l.relation, (l.source_id = %s) AS outgoing FROM concept_links l "
            "JOIN concepts c ON c.id = CASE WHEN l.source_id = %s THEN l.target_id ELSE l.source_id END "
            "WHERE l.source_id = %s OR l.target_id = %s ORDER BY l.weight DESC, c.label LIMIT %s",
            (concept_id, concept_id, concept_id, concept_id, neighbours),
        )
        around = cursor.fetchall()
        cursor.execute("SELECT concept_key, id FROM board_nodes WHERE project_id = %s AND kind = 'concept'", (project_id,))
        present = dict(cursor.fetchall())

    # New concepts go on a ring to the right of whatever is already on the board, the seeded concept in the middle.
    with connection.cursor() as cursor:
        cursor.execute("SELECT COALESCE(MAX(x), -600), COALESCE(MIN(y), 0) FROM board_nodes WHERE project_id = %s", (project_id,))
        right, top = cursor.fetchone()
    cx, cy = (right + 700.0, top + 350.0)
    fresh = [label for _, label, _, _ in around if label not in present]
    spots = dict(zip(fresh, ring_positions(len(fresh), cx, cy)))

    def node_for(label: str, at: Optional[Tuple[float, float]] = None) -> int:
        if label in present:
            return present[label]
        x, y = at or spots.get(label) or (cx, cy)
        created = add_node(connection, owner_id, project_id, "concept", concept_key=label, x=x, y=y)
        present[label] = created["id"]
        return created["id"]

    centre_node = node_for(centre[1], (cx, cy))
    added = 0
    for _, label, relation, outgoing in around:
        other = node_for(label)
        source, target = (centre_node, other) if outgoing else (other, centre_node)
        add_edge(connection, owner_id, project_id, source, target, "relates", RELATION_PHRASES.get(relation or "", "appears with"))
        added += 1
    return {"concept_node": centre_node, "neighbours": added}


def create_outline_draft(connection, owner_id: int, project_id: int) -> Dict[str, Any]:
    """A new draft in the project made from the board (see `outline_body`)."""
    import drafts  # pylint: disable=import-outside-toplevel

    board = get_board(connection, owner_id, project_id)
    if not any(node["kind"] in ("claim", "evidence") for node in board["nodes"]):
        raise BoardError("Add claims or evidence to the board first.")
    with connection.cursor() as cursor:
        cursor.execute("SELECT title FROM projects WHERE id = %s", (project_id,))
        title = cursor.fetchone()[0]
    body = outline_body(board["nodes"], board["edges"])
    return drafts.create_draft(connection, owner_id, f"Outline: {title}", body, "apa", project_id)
