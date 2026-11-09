"""fff.common.schema: the generated all_files view, the sorter's table list, snapshots, and
(integration) building a fresh install from the code."""
import copy
from types import SimpleNamespace

import pytest

from fff.common import config as fff_config
from fff.common import schema


def test_all_files_sql_lists_every_table_with_all_columns():
    sql = schema.all_files_sql("image_sorting_hashes", ["favorites", "nsfw_image"])
    assert sql.startswith("CREATE OR REPLACE VIEW `image_sorting_hashes`.`all_files` AS")
    assert sql.count("UNION ALL") == 1
    assert "'nsfw_image' AS `table_name` FROM `image_sorting_hashes`.`nsfw_image`" in sql
    for col in schema.ALL_FILES_COLUMNS:
        assert sql.count(f"`{col}`") == 2


def test_all_files_rejects_odd_names_and_empty_lists():
    with pytest.raises(ValueError):
        schema.all_files_sql("image_sorting_hashes", ["x`; DROP TABLE posts; --"])
    with pytest.raises(ValueError):
        schema.all_files_sql("image_sorting_hashes", [])


def test_union_order_puts_library_before_archives():
    names = ["other_directories", "archived_favorites", "archive_sfw_images", "nsfw_image",
             "manual_sort_gifs", "favorites", "zzz_new"]
    assert sorted(names, key=schema._union_rank) == [
        "favorites", "manual_sort_gifs", "nsfw_image", "archive_sfw_images", "archived_favorites",
        "other_directories", "zzz_new"]


def test_sorter_hash_table_names_cover_every_kind():
    cfg = SimpleNamespace(destination_directories={"nsfw_image": "x", "sfw_image": "y"},
                          archive_directories={"premium": "p", "sfw": {"images": "a", "videos": None}},
                          config={})
    names = schema.sorter_hash_table_names(cfg)
    assert names[:2] == ["nsfw_image", "sfw_image"]
    assert {"archive_premium", "archive_sfw_images", "archived_favorites", "manual_sort_gifs",
            "possible_missing_files"} <= set(names)
    assert "archive_sfw_videos" not in names and len(names) == len(set(names))


def test_snapshot_normalizes_counters_definers_and_test_names():
    sql = ("CREATE ALGORITHM=UNDEFINED DEFINER=`fff`@`%` SQL SECURITY DEFINER VIEW `all_files` AS "
           "select 1 from `test_image_sorting_hashes`.`favorites`")
    out = schema._normalize(sql, {"test_image_sorting_hashes": "image_sorting_hashes"})
    assert "DEFINER=`" not in out and "`image_sorting_hashes`.`favorites`" in out
    assert "AUTO_INCREMENT" not in schema._normalize(") ENGINE=InnoDB AUTO_INCREMENT=4512 DEFAULT", {})


@pytest.mark.integration
def test_fresh_install_from_code_matches_the_test_databases():
    """'fff db init' on empty databases builds the same tables and columns as test_*."""
    from fff.common import db

    live_cfg = fff_config.load()
    fresh_cfg = copy.deepcopy(live_cfg)
    fresh_cfg["databases"] = {m: f"fffinit_{n}" for m, n in live_cfg["databases"].items()}
    live, fresh = fff_config.database_names(live_cfg), fff_config.database_names(fresh_cfg)
    assert all(n.startswith("test_fffinit_") for n in fresh.values())

    def columns(conn, name):
        cur = conn.cursor()
        cur.execute("SELECT c.TABLE_NAME, c.COLUMN_NAME FROM information_schema.COLUMNS c "
                    "JOIN information_schema.TABLES t USING (TABLE_SCHEMA, TABLE_NAME) "
                    "WHERE c.TABLE_SCHEMA = %s AND t.TABLE_TYPE = 'BASE TABLE'", (name,))
        out = {}
        for table, column in cur.fetchall():
            out.setdefault(table, set()).add(column)
        return out

    try:
        fff_config.set_active(fresh_cfg)
        schema.init(progress=lambda msg: None)
        assert schema.check() == []
        conn = db.connect_server(live_cfg)
        try:
            for module in schema.MODULES:
                built, existing = columns(conn, fresh[module]), columns(conn, live[module])
                missing = {t: existing[t] - built.get(t, set()) for t in existing}
                assert not {t: c for t, c in missing.items() if c}, f"{module}: not built from code"
        finally:
            conn.close()
    finally:
        fff_config.set_active(live_cfg)
        conn = db.connect_server(live_cfg)
        cur = conn.cursor()
        for name in fresh.values():
            cur.execute(f"DROP DATABASE IF EXISTS `{name}`")
        conn.close()
