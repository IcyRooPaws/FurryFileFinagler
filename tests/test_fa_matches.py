"""An FA match whose file is already the FA download is moved, not downloaded again."""
from pathlib import Path

import pytest

from fff.fluffle import fa_matches, ops


@pytest.fixture(autouse=True)
def no_operation_db(monkeypatch):
    monkeypatch.setattr(ops, "operation_logger", lambda: None)


@pytest.mark.parametrize("name, post_id, expected", [
    ("12thedobie-general-FA-62034661.jpg", "62034661", True),
    ("some_artist-Adult-FA-123.png", "123", True),
    ("a-b-mature-FA-5.gif", "5", True),
    ("12thedobie-general-FA-62034661.jpg", "62034662", False),   # a different post
    ("12thedobie-general-FA-620346611.jpg", "62034661", False),
    ("artist-Adult-unk-FA-123.png", "123", False),               # deleted-post rename
    ("1302921337.doodlies_lucario.jpg", "1302921337", False),    # raw FA name
])
def test_is_named_fa_download(name, post_id, expected):
    assert fa_matches.is_named_fa_download(Path(name), post_id) is expected


def test_moves_named_file_to_the_download_folder(tmp_path):
    inbox, downloads, found = tmp_path / "inbox", tmp_path / "downloads", tmp_path / "found"
    inbox.mkdir()
    image = inbox / "artist-general-FA-42.jpg"
    image.write_bytes(b"original")

    assert fa_matches.move_named_fa_download(image, downloads, found, inbox)
    assert not image.exists()
    assert (downloads / image.name).read_bytes() == b"original"
    assert not found.exists()


def test_duplicate_of_a_file_already_downloaded_goes_to_source_found(tmp_path):
    inbox, downloads, found = tmp_path / "inbox", tmp_path / "downloads", tmp_path / "found"
    inbox.mkdir(); downloads.mkdir()
    image = inbox / "artist-general-FA-42.jpg"
    image.write_bytes(b"copy")
    (downloads / image.name).write_bytes(b"kept")

    assert fa_matches.move_named_fa_download(image, downloads, found, inbox)
    assert (downloads / image.name).read_bytes() == b"kept"
    assert (found / image.name).read_bytes() == b"copy"
    assert len(list(downloads.iterdir())) == 1


def test_file_already_in_the_download_folder_stays_put(tmp_path):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    image = downloads / "artist-general-FA-42.jpg"
    image.write_bytes(b"x")

    assert fa_matches.move_named_fa_download(image, downloads, tmp_path / "found")
    assert image.exists()
