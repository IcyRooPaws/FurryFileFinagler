"""fff.archive.generic_tags: filtering the review list, the generated query, and (integration)
saving classifications in MySQL."""
import sqlite3

import pytest

from fff.archive import generic_tags


@pytest.fixture(autouse=True)
def _no_default_list(monkeypatch, tmp_path):
    """data/kink_tags.txt is the user's own file; tests never read it."""
    monkeypatch.setattr(generic_tags, "DEFAULT_FETISH_FILE", tmp_path / "missing.txt")

TAGS = [
    {"tag_id": 1, "name": "mammal", "type": "Species", "posts": 900, "mark": "generic"},
    {"tag_id": 2, "name": "pirate_eagle", "type": "Character", "posts": 40, "mark": "specific"},
    {"tag_id": 3, "name": "fallow_deer", "type": "Species", "posts": 15, "mark": None},
    {"tag_id": 4, "name": "chastity_cage", "type": "General", "posts": 80, "mark": "fetish"},
]


def names(**filters):
    return [t["name"] for t in TAGS if generic_tags.matches(t, **filters)]


@pytest.mark.parametrize("status, expected", [
    ("todo", ["fallow_deer"]),
    ("generic", ["mammal"]),
    ("specific", ["pirate_eagle"]),
    ("fetish", ["chastity_cage"]),
    ("all", ["mammal", "pirate_eagle", "fallow_deer", "chastity_cage"]),
])
def test_matches_by_review_status(status, expected):
    assert names(status=status) == expected


def test_matches_by_type_case_insensitively():
    assert names(kind="species") == ["mammal", "fallow_deer"]


def test_search_treats_spaces_as_underscores():
    assert names(search="Fallow Deer") == ["fallow_deer"]
    assert names(search="eag", status="specific") == ["pirate_eagle"]


# The generated query runs as-is on SQLite too, so check it by what it returns.
@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE tags (tag_id INTEGER PRIMARY KEY, name TEXT, tag_type TEXT);
        CREATE TABLE posts_tags (post_id INTEGER, tag_id INTEGER);
        CREATE TABLE tag_classifications (tag_id INTEGER PRIMARY KEY, is_generic INTEGER, is_fetish INTEGER);
    """)
    conn.executemany("INSERT INTO tags VALUES (?, ?, ?)",
                     [(1, "mammal", "Species"), (2, "pirate_eagle", "Character"), (3, "fallow_deer", "Species"),
                      (4, "solo", "General"), (5, "comic", "Meta"), (6, "o'neil", "Character"), (7, "odd", None),
                      (8, "chastity_cage", "General")])
    conn.executemany("INSERT INTO posts_tags VALUES (?, ?)", [(10, 1), (10, 2), (10, 4), (11, 1), (11, 3)])
    conn.executemany("INSERT INTO tag_classifications VALUES (?, ?, ?)", [(1, 1, 0), (2, 0, 0), (6, 1, 0), (8, 0, 1)])
    yield conn
    conn.close()


def run(db, sql):
    tag_query, post_query = [q for q in sql.split(";") if q.strip()]
    return ([row[1] for row in db.execute(tag_query + " ORDER BY t.tag_id")],
            sorted((row[0], row[1]) for row in db.execute(post_query)))


def test_live_query_leaves_out_tags_marked_generic(db):
    tags, post_tags = run(db, generic_tags.filter_sql())
    assert tags == ["pirate_eagle", "fallow_deer", "solo", "comic", "odd", "chastity_cage"]  # fetish isn't generic
    assert post_tags == [(10, "pirate_eagle"), (10, "solo"), (11, "fallow_deer")]


def test_query_can_also_leave_out_general_and_meta_types(db):
    tags, _ = run(db, generic_tags.filter_sql(skip_generic_types=True))
    assert tags == ["pirate_eagle", "fallow_deer"]


def test_inline_query_needs_no_classification_table_and_escapes_names(db):
    db.execute("DROP TABLE tag_classifications")
    sql = generic_tags.filter_sql(inline_names=["mammal", "o'neil"])
    assert "tag_classifications" not in sql
    tags, _ = run(db, sql)
    assert tags == ["pirate_eagle", "fallow_deer", "solo", "comic", "odd", "chastity_cage"]


def test_inline_query_with_nothing_marked_is_still_valid(db):
    tags, _ = run(db, generic_tags.filter_sql(inline_names=[]))
    assert len(tags) == 8


def test_load_tags_counts_posts_and_reads_classifications(db):
    loaded = {t["name"]: t for t in generic_tags.load_tags(db)}
    assert loaded["mammal"] == {"tag_id": 1, "name": "mammal", "type": "Species", "posts": 2,
                                "saved": "generic", "listed": False, "mark": "generic"}
    assert loaded["pirate_eagle"]["mark"] == "specific"
    assert loaded["chastity_cage"]["mark"] == "fetish"
    assert loaded["comic"]["posts"] == 0 and loaded["comic"]["mark"] is None
    assert loaded["odd"]["type"] == "General"
    assert next(iter(loaded)) == "mammal"  # most-used first


def test_default_list_reads_names_comments_and_species_prefix(tmp_path):
    listed = tmp_path / "kink_tags.txt"
    listed.write_text("# fetish tags\n\nVore\nspecies:Taur  # a species tag\n  hyper  \n", encoding="utf-8")
    assert generic_tags.default_fetish_names(listed) == {"vore", "taur", "hyper"}
    assert generic_tags.default_fetish_names(tmp_path / "missing.txt") == frozenset()


def test_listed_tags_default_to_fetish_but_saved_marks_win(db):
    loaded = {t["name"]: t for t in generic_tags.load_tags(db, frozenset({"solo", "mammal", "comic"}))}
    assert (loaded["solo"]["saved"], loaded["solo"]["listed"], loaded["solo"]["mark"]) == (None, True, "fetish")
    assert loaded["mammal"]["mark"] == "generic"  # saved generic beats the list
    assert loaded["fallow_deer"]["mark"] is None  # not listed, not reviewed
    tag = loaded["solo"]
    tag["saved"] = "specific"
    assert generic_tags.effective_mark(tag) == "specific"
    tag["saved"] = None  # clearing goes back to the list default
    assert generic_tags.effective_mark(tag) == "fetish"


def test_load_tags_reads_the_default_file(db, tmp_path, monkeypatch):
    listed = tmp_path / "kink_tags.txt"
    listed.write_text("comic\n", encoding="utf-8")
    monkeypatch.setattr(generic_tags, "DEFAULT_FETISH_FILE", listed)
    assert [t["name"] for t in generic_tags.load_tags(db) if t["listed"]] == ["comic"]


def test_set_mark_rejects_unknown_marks():
    with pytest.raises(ValueError):
        generic_tags.set_mark(None, [1], "spicy")


@pytest.mark.integration
def test_set_mark_round_trip_in_mysql():
    from fff.archive.config import load_config
    from fff.archive.db import connect_to_mysql

    conn = connect_to_mysql(load_config()["mysql"])
    try:
        generic_tags._table_ready = False
        generic_tags.ensure_table(conn)
        cursor = conn.cursor()
        cursor.execute("SELECT tag_id FROM tags ORDER BY tag_id LIMIT 2")
        ids = [row[0] for row in cursor.fetchall()]
        marks = {tag["tag_id"]: tag["mark"] for tag in generic_tags.load_tags(conn) if tag["tag_id"] in ids}
        try:
            def state():
                cursor.execute(f"SELECT tag_id, is_generic, is_fetish FROM {generic_tags.TABLE} "
                               "WHERE tag_id IN (%s, %s) ORDER BY tag_id", ids)
                return cursor.fetchall()

            generic_tags.set_mark(conn, ids, "generic")
            assert state() == [(ids[0], 1, 0), (ids[1], 1, 0)]
            generic_tags.set_mark(conn, ids[1:], "fetish")  # update in place
            assert state() == [(ids[0], 1, 0), (ids[1], 0, 1)]
            generic_tags.set_mark(conn, ids[:1], "specific")
            assert state() == [(ids[0], 0, 0), (ids[1], 0, 1)]
            assert {t["tag_id"]: t["mark"] for t in generic_tags.load_tags(conn) if t["tag_id"] in ids} == {
                ids[0]: "specific", ids[1]: "fetish"}
            generic_tags.set_mark(conn, ids, None)
            assert state() == []
        finally:
            generic_tags.set_mark(conn, ids, None)
            for tag_id, mark in marks.items():
                generic_tags.set_mark(conn, [tag_id], mark)
        cursor.close()
    finally:
        conn.close()
