"""fff.gui.thumbnails and the archive browser's /thumb endpoint."""
import os
import time

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from fff.gui import thumbnails  # noqa: E402


@pytest.fixture(autouse=True)
def thumb_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(thumbnails, "THUMB_DIR", tmp_path / "thumbs")
    return tmp_path / "thumbs"


def opened(path):
    with Image.open(path) as image:
        return image.format, image.size, image.mode


@pytest.mark.parametrize("name, mode, size", [
    ("photo.jpg", "RGB", (3000, 2000)),
    ("art.png", "RGBA", (1200, 2400)),
    ("small.png", "RGB", (200, 100)),  # never enlarged
])
def test_images_become_small_webp_previews(tmp_path, name, mode, size):
    source = tmp_path / name
    Image.new(mode, size, (200, 100, 50, 128) if mode == "RGBA" else (200, 100, 50)).save(source)
    preview = thumbnails.thumbnail(source)
    fmt, (w, h), out_mode = opened(preview)
    assert fmt == "WEBP"
    assert max(w, h) == min(thumbnails.SIZE, max(size))
    assert abs(w / h - size[0] / size[1]) < 0.02
    assert out_mode == ("RGBA" if mode == "RGBA" else "RGB")  # transparency kept
    assert preview.stat().st_size < 20_000


def test_animated_gif_uses_its_first_frame(tmp_path):
    source = tmp_path / "anim.gif"
    frames = [Image.new("RGB", (500, 500), color) for color in ((255, 0, 0), (0, 0, 255))]
    frames[0].save(source, save_all=True, append_images=frames[1:], duration=100, loop=0)
    with Image.open(thumbnails.thumbnail(source)) as preview:
        r, g, b = preview.convert("RGB").getpixel((10, 10))
    assert r > 200 and b < 60


def test_video_preview_is_a_frame(tmp_path):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    source = tmp_path / "clip.avi"
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"MJPG"), 10, (640, 360))
    for _ in range(30):
        writer.write(np.full((360, 640, 3), (0, 200, 0), dtype=np.uint8))  # BGR green
    writer.release()
    preview = thumbnails.thumbnail(source, video=True)
    assert preview is not None
    with Image.open(preview) as image:
        assert image.size == (360, 202) or image.size == (360, 203)
        r, g, b = image.convert("RGB").getpixel((100, 100))
    assert g > 150 and r < 80 and b < 80


def test_unreadable_or_missing_files_have_no_preview(tmp_path):
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not an image")
    assert thumbnails.thumbnail(broken) is None
    assert thumbnails.thumbnail(tmp_path / "missing.png") is None
    assert thumbnails.thumbnail(broken, video=True) is None


def test_previews_are_reused_until_the_file_changes(tmp_path):
    source = tmp_path / "a.png"
    Image.new("RGB", (800, 800), (255, 0, 0)).save(source)
    first = thumbnails.thumbnail(source)
    made = first.stat().st_mtime_ns
    time.sleep(0.01)
    assert thumbnails.thumbnail(source) == first and first.stat().st_mtime_ns == made  # not remade
    Image.new("RGB", (800, 400), (0, 0, 255)).save(source)
    os.utime(source, ns=(time.time_ns(), time.time_ns() + 1_000_000_000))
    second = thumbnails.thumbnail(source)
    assert second != first
    assert opened(second)[1] == (360, 180)


# --------------------------------------------------------------------------- endpoint

def test_thumb_endpoint_serves_a_cached_webp(tmp_path, monkeypatch):
    pytest.importorskip("flask")
    from fff.gui import archive_browser

    source = tmp_path / "post.jpg"
    Image.new("RGB", (2000, 1000), (10, 20, 30)).save(source)
    lookups = []
    monkeypatch.setattr(archive_browser, "_known_paths", archive_browser.OrderedDict())
    monkeypatch.setattr(archive_browser, "post_file_path",
                        lambda pid: lookups.append(pid) or (str(source) if pid == 7 else None))
    client = archive_browser.app.test_client()

    archive_browser.remember_path(7, str(source))      # as the post listing does
    response = client.get("/thumb/7")
    assert response.status_code == 200 and response.mimetype == "image/webp"
    cache = response.headers["Cache-Control"]
    assert "max-age=604800" in cache and "private" in cache and "public" not in cache
    assert lookups == []                                # path came from the listing, no DB query
    assert client.get("/thumb/8").status_code == 404     # no file for this post
    assert lookups == [8]


def test_a_remembered_path_that_moved_is_looked_up_again(tmp_path, monkeypatch):
    pytest.importorskip("flask")
    from fff.gui import archive_browser

    moved = tmp_path / "new.png"
    Image.new("RGB", (100, 100)).save(moved)
    monkeypatch.setattr(archive_browser, "_known_paths", archive_browser.OrderedDict())
    monkeypatch.setattr(archive_browser, "post_file_path", lambda pid: str(moved))
    archive_browser.remember_path(3, str(tmp_path / "old.png"))  # the sorter moved it since
    assert archive_browser.known_file_path(3) == str(moved)
