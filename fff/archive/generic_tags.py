"""How the user has classified e621 tags: generic, not generic, or fetish.

"Generic" tags are on so many posts they say little (mammal, canine, ...). e621 has no
fetish tag type (those tags are General), so fetish tags are marked by hand too; a
fetish tag is never generic.

The archive keeps one row per reviewed tag in ``tag_classifications``; tags nobody has
reviewed have no row and count as not generic. The Generic tags tool
(fff.gui.generic_tags_ui) fills the table, the archive browser's trending lists read it,
and ``filter_sql`` writes a MySQL query that leaves the generic tags out.
"""
from __future__ import annotations

TABLE = "tag_classifications"

# A tag's mark: one of these, or None while it hasn't been reviewed.
GENERIC, SPECIFIC, FETISH = "generic", "specific", "fetish"
MARKS = (GENERIC, SPECIFIC, FETISH)

# Tag types that describe content or format rather than a subject. The query can
# leave these out wholesale (General has 22k+ tags, too many to review one by one).
GENERIC_TYPES = ("general", "meta")

_table_ready = False


def ensure_table(conn) -> None:
    """Create tag_classifications (or add columns it gained) once per process; also run by 'fff db init'."""
    global _table_ready
    if _table_ready:
        return
    cursor = conn.cursor()
    try:
        cursor.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE} (
            tag_id INT PRIMARY KEY,
            is_generic TINYINT(1) NOT NULL,
            is_fetish TINYINT(1) NOT NULL DEFAULT 0,
            classified_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            FOREIGN KEY (tag_id) REFERENCES tags(tag_id) ON DELETE CASCADE
        );""")
        cursor.execute("SELECT COUNT(*) FROM information_schema.COLUMNS"
                       " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = 'is_fetish'",
                       (TABLE,))
        if not cursor.fetchone()[0]:  # created before fetish marks existed
            cursor.execute(f"ALTER TABLE {TABLE} ADD COLUMN is_fetish TINYINT(1) NOT NULL DEFAULT 0 AFTER is_generic")
        conn.commit()
        _table_ready = True
    finally:
        cursor.close()


def _mark(is_generic, is_fetish) -> str | None:
    if is_generic is None:
        return None
    return GENERIC if is_generic else FETISH if is_fetish else SPECIFIC


def load_tags(conn) -> list[dict]:
    """Every tag with its post count and mark (see MARKS; None = not reviewed), most-used first."""
    cursor = conn.cursor()
    try:
        cursor.execute(f"""
            SELECT t.tag_id, t.name, COALESCE(t.tag_type, 'General'), COALESCE(u.uses, 0),
                   tc.is_generic, tc.is_fetish
            FROM tags t
            LEFT JOIN (SELECT tag_id, COUNT(*) AS uses FROM posts_tags GROUP BY tag_id) u ON u.tag_id = t.tag_id
            LEFT JOIN {TABLE} tc ON tc.tag_id = t.tag_id
            ORDER BY uses DESC, t.name
        """)
        return [{"tag_id": tag_id, "name": name, "type": kind, "posts": int(uses), "mark": _mark(generic, fetish)}
                for tag_id, name, kind, uses, generic, fetish in cursor.fetchall()]
    finally:
        cursor.close()


def set_mark(conn, tag_ids, mark: str | None) -> None:
    """Mark tags generic, not generic (specific) or fetish, or forget the decision (None)."""
    if mark is not None and mark not in MARKS:
        raise ValueError(f"unknown mark {mark!r}")
    tag_ids = list(tag_ids)
    if not tag_ids:
        return
    cursor = conn.cursor()
    try:
        if mark is None:
            cursor.execute(f"DELETE FROM {TABLE} WHERE tag_id IN ({', '.join(['%s'] * len(tag_ids))})", tag_ids)
        else:
            cursor.executemany(
                f"INSERT INTO {TABLE} (tag_id, is_generic, is_fetish) VALUES (%s, %s, %s)"
                " ON DUPLICATE KEY UPDATE is_generic = VALUES(is_generic), is_fetish = VALUES(is_fetish)",
                [(tag_id, int(mark == GENERIC), int(mark == FETISH)) for tag_id in tag_ids])
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        cursor.close()


def matches(tag: dict, *, kind: str = "", status: str = "all", search: str = "") -> bool:
    """Whether a tag from load_tags passes the tool's filters.

    status: 'todo' (not reviewed), one of MARKS, or 'all'.
    search: substring of the name; spaces match underscores.
    """
    if kind and tag["type"].lower() != kind.lower():
        return False
    if status == "todo" and tag["mark"] is not None:
        return False
    if status in MARKS and tag["mark"] != status:
        return False
    search = search.strip().lower().replace(" ", "_")
    return not search or search in tag["name"].lower()


def _sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def filter_sql(*, inline_names: list[str] | None = None, skip_generic_types: bool = False) -> str:
    """A MySQL query listing tags with the generic ones left out, plus the same filter on posts_tags.

    By default the query reads tag_classifications, so it stays current as tags are reviewed.
    Pass ``inline_names`` (the generic tag names) for a self-contained version that only
    needs the tags table, e.g. for a database without tag_classifications.
    """
    conditions = []
    if inline_names is not None:
        if inline_names:
            names = ",\n    ".join(_sql_string(n) for n in sorted(inline_names))
            conditions.append(f"t.name NOT IN (\n    {names}\n)")
            source = f"the {len(inline_names):,} generic tags named in the query"
        else:
            source = "nothing yet: no tags are marked generic"
    else:
        conditions.append(f"NOT EXISTS (\n    SELECT 1 FROM {TABLE} tc\n"
                          "    WHERE tc.tag_id = t.tag_id AND tc.is_generic = 1\n)")
        source = f"tags marked generic in {TABLE} (unreviewed tags count as not generic)"
    if skip_generic_types:
        types = ", ".join(_sql_string(t) for t in GENERIC_TYPES)
        conditions.append(f"COALESCE(LOWER(t.tag_type), 'general') NOT IN ({types})")
        source += f", plus every {' and '.join(t.title() for t in GENERIC_TYPES)} tag"
    where = ("\nWHERE " + "\n  AND ".join(conditions)) if conditions else ""
    return (f"-- Leaves out {source}.\n"
            f"SELECT t.tag_id, t.name, t.tag_type\nFROM tags t{where};\n\n"
            f"-- The same filter on each post's tags:\n"
            f"SELECT pt.post_id, t.name, t.tag_type\nFROM posts_tags pt\nJOIN tags t ON t.tag_id = pt.tag_id{where};\n")
