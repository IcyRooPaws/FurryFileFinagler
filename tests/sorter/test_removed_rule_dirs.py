from fff.sorter.features import removed_rule_dirs as rrd


def _touch(path, text='x'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


def test_only_folders_no_rule_uses_any_more_are_removed(tmp_path):
    kept, gone = tmp_path / 'Fav' / 'Yoshi', tmp_path / 'Fav' / 'Koopas'
    shared = tmp_path / 'Fav' / 'Guzame'
    old = [str(kept), str(gone), str(shared), str(shared)]
    new = [str(kept), str(shared)]

    assert rrd.find_removed_directories(old, new) == [str(gone)]


def test_core_folders_and_their_parents_are_never_emptied(tmp_path):
    fav = tmp_path / 'Fav'
    old = [str(fav), str(tmp_path), str(fav / 'Koopas')]

    removed = rrd.find_removed_directories(old, [], never_empty=[str(fav)])

    assert removed == [str(fav / 'Koopas')]


def test_folders_inside_another_removed_folder_are_folded_in(tmp_path):
    parent = tmp_path / 'Pokemon'
    old = [str(parent), str(parent / 'Knotting')]

    assert rrd.find_removed_directories(old, []) == [str(parent)]


def test_list_files_skips_subfolders_a_remaining_rule_still_uses(tmp_path):
    gone = tmp_path / 'Pokemon'
    a = _touch(gone / 'a.png')
    b = _touch(gone / 'Old Sub' / 'b.png')
    _touch(gone / 'Still Used' / 'c.png')

    files = rrd.list_files([str(gone)], keep_dirs=[str(gone / 'Still Used')])

    assert sorted(files) == sorted([str(a), str(b)])


def test_removed_sub_rule_inside_a_kept_parent_is_still_listed(tmp_path):
    parent = tmp_path / 'Pokemon'
    _touch(parent / 'kept.png')
    moved = _touch(parent / 'Knotting' / 'k.png')

    files = rrd.list_files([str(parent / 'Knotting')], keep_dirs=[str(parent)])

    assert files == [str(moved)]


def test_move_to_queue_renames_clashes_and_cleans_up_empty_folders(tmp_path):
    gone = tmp_path / 'Koopas'
    queue = tmp_path / 'Need to be Sorted'
    _touch(queue / 'same.png', 'already here')
    _touch(gone / 'same.png', 'from rule')
    _touch(gone / 'Sub' / 'other.png')

    files = rrd.list_files([str(gone)])
    moved, failed = rrd.move_files_to_queue(files, str(queue))
    rrd.remove_empty_folders([str(gone)])

    assert (moved, failed) == (2, [])
    assert (queue / 'same.png').read_text(encoding='utf-8') == 'already here'
    assert (queue / 'same_1.png').read_text(encoding='utf-8') == 'from rule'
    assert (queue / 'other.png').exists()
    assert not gone.exists()


def test_protected_files_are_left_alone(tmp_path, monkeypatch):
    gone = tmp_path / 'Koopas'
    _touch(gone / 'commission.png')
    monkeypatch.setattr(rrd.protected, 'is_protected', lambda p: str(p).startswith(str(gone)))

    assert rrd.list_files([str(gone)]) == []
