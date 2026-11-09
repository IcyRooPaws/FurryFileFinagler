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


# --- FA Tag Blocking -----------------------------------------------------------------

from fff.clients import furaffinity  # noqa: E402

FA_PAGE = """
<body class="c-bodyColor" data-tag-blocklist="Scat  vore_belly human" data-tag-blocklist-hide-tagless="0">
<img class="blocked-content" data-tags="u_artist c_artwork_digital t_general_furry_art s_wolf Wolf Human male"
     id="submissionImg" alt="x" data-fullview-src="//d.furaffinity.net/art/artist/1/1.artist_x.png">
<img class="blocked-content" data-tags="u_artist scat" src="//t.furaffinity.net/2@200-2.jpg">
<span class="tags"><a href="/search/@keywords wolf">wolf</a></span>
</body>"""


def test_parse_fa_tags_reads_the_submission_and_the_account_blocklist():
    tags, blocklist = furaffinity.parse_fa_tags(FA_PAGE)
    assert tags == ["u_artist", "c_artwork_digital", "t_general_furry_art", "s_wolf", "wolf", "human", "male"]
    assert blocklist == ["scat", "vore_belly", "human"]
    # "scat" is only on a related-submission thumbnail, so it does not count
    assert furaffinity.blocked_fa_tags({"tags": tags, "tag_blocklist": blocklist}) == ["human"]


def test_parse_fa_tags_falls_back_to_keyword_links():
    page = '<body data-tag-blocklist="wolf"><span class="tags"><a href="/search/@keywords Wolf">Wolf</a></span>'
    assert furaffinity.parse_fa_tags(page) == (["wolf"], ["wolf"])


def test_no_blocklist_blocks_nothing():
    tags, blocklist = furaffinity.parse_fa_tags('<img id="submissionImg" data-tags="scat">')
    assert blocklist == [] and furaffinity.blocked_fa_tags({"tags": tags, "tag_blocklist": blocklist}) == []


def test_blocked_post_moves_to_blacklisted(tmp_path, capsys):
    inbox, blacklisted = tmp_path / "inbox", tmp_path / "blacklisted"
    inbox.mkdir()
    image = inbox / "1.artist_x.png"
    image.write_bytes(b"x")
    post = {"post_id": "1", "tags": ["wolf", "human"], "tag_blocklist": ["human"]}
    assert fa_matches.move_if_fa_blocklisted(image, post, blacklisted, inbox) is True
    assert (blacklisted / image.name).exists() and not image.exists()
    assert "FA blocklist: human" in capsys.readouterr().out
    assert fa_matches.move_if_fa_blocklisted(image, {"tags": ["wolf"], "tag_blocklist": ["human"]},
                                             blacklisted, inbox) is None


def test_blocked_post_is_not_favorited_or_downloaded(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    image = inbox / "1.artist_x.png"
    image.write_bytes(b"x")
    monkeypatch.setattr(fa_matches, "get_fa_post_data", lambda post_id, cookies: {
        "post_id": post_id, "tags": ["scat"], "tag_blocklist": ["scat"], "download_url": "u"})

    def must_not_call(*args, **kwargs):
        raise AssertionError("blocked post was favorited or downloaded")
    for name in ("check_if_fa_favorited", "add_to_fa_favorites", "download_fa_post"):
        monkeypatch.setattr(fa_matches, name, must_not_call)
    unfaved = []
    monkeypatch.setattr(fa_matches, "remove_from_fa_favorites", lambda post_id, cookies: unfaved.append(post_id) or True)

    config = {"e621": {"blacklisted_directory": str(tmp_path / "blacklisted")},
              "search": {"image_directories": [str(inbox)]}}
    assert fa_matches.process_single_fa_image(image, "fa_1", config, download_dir=tmp_path / "dl",
                                              source_found_dir=tmp_path / "found", cookies=object()) is True
    assert (tmp_path / "blacklisted" / image.name).exists()
    assert unfaved == ["1"]  # an already-faved blocked post gets unfavorited


def test_blocked_post_still_moves_when_unfavorite_fails(tmp_path, monkeypatch, capsys):
    inbox, blacklisted = tmp_path / "inbox", tmp_path / "blacklisted"
    inbox.mkdir()
    image = inbox / "1.artist_x.png"
    image.write_bytes(b"x")

    def fa_blocked_us(post_id, cookies):
        raise RuntimeError("403")
    monkeypatch.setattr(fa_matches, "remove_from_fa_favorites", fa_blocked_us)
    post = {"post_id": "1", "tags": ["human"], "tag_blocklist": ["human"]}
    assert fa_matches.move_if_fa_blocklisted(image, post, blacklisted, inbox, cookies=object()) is True
    assert (blacklisted / image.name).exists()
    assert "Removing from FA favorites (if favorited)... [ERROR] (403)" in capsys.readouterr().out
