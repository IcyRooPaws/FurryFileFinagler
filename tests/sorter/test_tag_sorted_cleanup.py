from types import SimpleNamespace

from fff.sorter.features.tag_sorted_cleanup import cleanup_tag_sorted_directories


def test_cleanup_removes_nested_empty_folders_but_preserves_root(tmp_path):
    root = tmp_path / 'Tag Sorted'
    empty_leaf = root / 'Characters' / 'Unused'
    empty_leaf.mkdir(parents=True)
    (root / 'Characters' / 'kept.txt').write_text('keep', encoding='utf-8')

    config = SimpleNamespace(
        get_tag_sorting_base_dirs=lambda: {'pictures': str(root), 'videos': '', 'gifs': ''},
        get_tag_sorting_config=lambda: {'rules': []},
    )

    deleted, skipped = cleanup_tag_sorted_directories(config)

    assert (deleted, skipped) == (1, 0)
    assert root.is_dir()
    assert not empty_leaf.exists()
    assert (root / 'Characters').is_dir()


def test_cleanup_dry_run_does_not_remove_folders(tmp_path):
    root = tmp_path / 'Tag Sorted'
    empty_folder = root / 'Unused'
    empty_folder.mkdir(parents=True)
    config = SimpleNamespace(
        get_tag_sorting_base_dirs=lambda: {'pictures': str(root)},
        get_tag_sorting_config=lambda: {'rules': []},
    )

    deleted, skipped = cleanup_tag_sorted_directories(config, dry_run=True)

    assert (deleted, skipped) == (1, 0)
    assert empty_folder.is_dir()