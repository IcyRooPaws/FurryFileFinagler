"""Small cached previews for the archive browser's grid.

The grid used to load every post's original file to show a ~170 px preview: about 150 MB
for one page, every image decoded at full size, and every video opened as a player that
seeks to a frame. A preview here is a 360 px WebP (sharp on high-DPI screens), typically
5-30 KB, made once and kept in data/cache/thumbs.

The cache key is the file's path, size and modification time, so a file that is moved,
replaced or edited gets a fresh preview; old ones are simply never asked for again.
"""
from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

from fff.common import paths

THUMB_DIR = paths.CACHE_DIR / "thumbs"
SIZE = 360
QUALITY = 80

# Decoding big images is CPU- and memory-heavy; a page asks for ~48 at once on a cold cache.
_workers = threading.BoundedSemaphore(max(2, (os.cpu_count() or 4) // 2))
# Two requests for the same new preview make it once. A fixed set of locks shared by key hash,
# rather than one lock per preview, which would grow with every file ever shown.
_locks = tuple(threading.Lock() for _ in range(64))


def cache_path(source: str | Path) -> Path:
    """Where the preview of ``source`` is (or will be) stored. Raises OSError if it's gone."""
    st = os.stat(source)
    key = hashlib.sha1(f"{os.path.normcase(os.path.abspath(source))}|{st.st_size}|{st.st_mtime_ns}"
                       .encode("utf-8")).hexdigest()
    return THUMB_DIR / key[:2] / f"{key}.webp"


def thumbnail(source: str | Path, *, video: bool = False) -> Path | None:
    """The cached preview for ``source``, made now if needed; None if it can't be read."""
    try:
        target = cache_path(source)
    except OSError:
        return None
    if target.exists():
        return target
    with _lock_for(str(target)):
        if target.exists():
            return target
        with _workers:
            image = _video_frame(source) if video else _image(source)
        if image is None:
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(f"{target.stem}.{os.getpid()}.{threading.get_ident()}.partial")
        try:
            image.save(partial, "WEBP", quality=QUALITY, method=4)
            os.replace(partial, target)
        except OSError:
            partial.unlink(missing_ok=True)
            return None
    return target


def _lock_for(key: str) -> threading.Lock:
    return _locks[int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], 16) % len(_locks)]


def _fit(image):
    from PIL import Image

    image.thumbnail((SIZE, SIZE), Image.Resampling.LANCZOS)
    keeps_alpha = image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info)
    return image.convert("RGBA" if keeps_alpha else "RGB")


def _image(source):
    from PIL import Image, ImageOps

    try:
        with Image.open(source) as image:
            image.draft("RGB", (SIZE * 2, SIZE * 2))  # JPEG: decode at a reduced scale, much faster
            return _fit(ImageOps.exif_transpose(image))  # first frame for GIFs; phone photos upright
    except Exception:  # unreadable or unsupported file: the grid shows its "No file" box
        return None


def _video_frame(source):
    """A frame 10% into the video (past black intros), or the first frame if seeking fails."""
    os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")  # damaged webms spam FFmpeg errors
    try:
        import cv2
        from PIL import Image
    except ImportError:
        return None
    capture = cv2.VideoCapture(str(source))
    try:
        if not capture.isOpened():
            return None
        frames = capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        if frames > 10:
            capture.set(cv2.CAP_PROP_POS_FRAMES, int(frames * 0.1))
        ok, frame = capture.read()
        if not ok:
            capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = capture.read()
        if not ok or frame is None:
            return None
    except Exception:
        return None
    finally:
        capture.release()
    return _fit(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
