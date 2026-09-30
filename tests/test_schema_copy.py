import pytest

from fff.common import schema_copy

CFG = {"database": {"host": "h", "user": "u", "password": "p"},
       "databases": {"fluffle": "image_search", "archive": "e621_posts_db", "sorter": "image_sorting_hashes"}}
REFS = {"image_search": "test_image_search", "e621_posts_db": "test_e621_posts_db",
        "image_sorting_hashes": "test_image_sorting_hashes"}


def test_cross_database_references_all_rewritten():
    view = ("CREATE VIEW `all_deleted` AS select `a`.`file_name` from `image_sorting_hashes`.`all_files` `a` "
            "join `e621_posts_db`.`posts` `p` on `p`.`id` = `a`.`e621_post_id`")
    out = schema_copy.rewrite_database_references(view, REFS)
    assert "`test_image_sorting_hashes`.`all_files`" in out and "`test_e621_posts_db`.`posts`" in out
    assert "`image_sorting_hashes`" not in out and "`e621_posts_db`" not in out


def test_rewrite_is_idempotent_and_leaves_test_names_alone():
    sql = "select * from `test_e621_posts_db`.`posts` join `e621_posts_db`.`tags`"
    once = schema_copy.rewrite_database_references(sql, REFS)
    assert once == "select * from `test_e621_posts_db`.`posts` join `test_e621_posts_db`.`tags`"
    assert schema_copy.rewrite_database_references(once, REFS) == once


def test_original_single_db_rewrite_misses_other_databases():
    """Documents why fff rewrites all three: the original only rewrote the copied DB."""
    sql = "select * from `image_sorting_hashes`.`t` join `e621_posts_db`.`posts`"
    out = schema_copy.update_database_references(sql, "image_sorting_hashes", "test_image_sorting_hashes")
    assert "`e621_posts_db`" in out


@pytest.mark.parametrize("target", ["e621_posts_db", "image_sorting_hashes", "image_search", "e621_backup",
                                    "prod_copy"])
def test_unsafe_targets_refused(target):
    with pytest.raises(schema_copy.UnsafeTargetError):
        schema_copy.check_target("e621_posts_db", target, CFG)


def test_test_targets_allowed_and_pairs():
    schema_copy.check_target("e621_posts_db", "test_e621_posts_db", CFG)
    assert schema_copy.test_database_pairs(CFG, ["archive"]) == [("archive", "e621_posts_db", "test_e621_posts_db")]
    assert [p[2] for p in schema_copy.test_database_pairs(CFG)] == list(REFS.values())


def test_copy_refuses_before_connecting(monkeypatch):
    monkeypatch.setattr(schema_copy.mysql.connector, "connect", lambda **k: pytest.fail("connected"))
    with pytest.raises(schema_copy.UnsafeTargetError):
        schema_copy.copy_database_schema(CFG, "e621_posts_db", "e621_posts_db")


def test_copy_data_refuses_unsafe_target_before_connecting(monkeypatch):
    monkeypatch.setattr(schema_copy.mysql.connector, "connect", lambda **k: pytest.fail("connected"))
    for target in ("e621_posts_db", "image_sorting_hashes", "backup_e621"):
        with pytest.raises(schema_copy.UnsafeTargetError):
            schema_copy.copy_database_data(CFG, "e621_posts_db", target)


def test_copy_data_cli_requires_confirmation(monkeypatch, capsys):
    from fff.cli.app import main
    from fff.common import config as fff_config

    monkeypatch.setattr(fff_config, "load", lambda *a, **k: CFG)
    monkeypatch.setattr(schema_copy, "copy_all_data", lambda *a, **k: pytest.fail("copied without confirmation"))
    monkeypatch.setattr("builtins.input", lambda prompt="": "no")
    assert main(["db", "copy-data", "--db", "archive"]) == 1
    assert "Cancelled" in capsys.readouterr().out
