"""
Video similarity detection - finds the same video at different quality levels.

Byte hashes and single-frame perceptual hashes can't match a 1080p original
against a 480p letterboxed re-encode, a trimmed cut, or a different edit.
This module builds a per-video signature instead:

* N frames sampled evenly across the video's length
* black borders (letterbox/pillarbox) cropped from each frame before hashing
* a 64-bit pHash per frame

Two videos are compared by taking, for each frame in one, the closest frame in
the other, and using the median of those distances. Checking both directions
and keeping the lower result tolerates trims, excerpts, and re-edits, so no
duration filter is applied.

Distances are 0-64 bits. Calibrated on real HQ/LQ pairs: same-content pairs
scored 2-16, unrelated videos 18+.
"""

import hashlib
import json
import logging
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import numpy as np

# Silence FFmpeg's console output ("File is broken, keyframes not correctly marked!" etc.)
# from damaged webms - frames still decode. Only takes effect if set before cv2 is imported.
os.environ.setdefault('OPENCV_FFMPEG_LOGLEVEL', '-8')

try:
    import cv2
    import imagehash
    from PIL import Image
    HAS_VIDEO_SIMILARITY = True
except ImportError:  # pragma: no cover - optional deps
    HAS_VIDEO_SIMILARITY = False

logger = logging.getLogger(__name__)

# Bump when the signature algorithm changes so cached signatures are recomputed
SIGNATURE_VERSION = 1
FRAME_COUNT = 16
DEFAULT_MAX_DISTANCE = 16

VIDEO_EXTENSIONS = {'.mp4', '.webm', '.mov', '.avi', '.mkv', '.m4v'}

# Popcount lookup for numpy < 2.0 (no np.bitwise_count)
_POPCOUNT8 = np.array([bin(i).count('1') for i in range(256)], dtype=np.uint8)


@dataclass
class VideoSignature:
    """Frame hashes plus the stream metadata used to rank quality."""
    width: int = 0
    height: int = 0
    codec: str = ''
    duration: float = 0.0
    bitrate: int = 0
    frame_hashes: List[int] = field(default_factory=list)

    @property
    def pixels(self) -> int:
        return self.width * self.height

    def quality_key(self) -> Tuple[int, int]:
        """Higher is better: resolution first, then bitrate."""
        return (self.pixels, self.bitrate)

    def describe(self) -> str:
        parts = []
        if self.width and self.height:
            parts.append(f"{self.width}x{self.height}")
        if self.bitrate:
            parts.append(f"{self.bitrate / 1_000_000:.1f} Mbps")
        if self.codec:
            parts.append(self.codec)
        if self.duration:
            parts.append(f"{self.duration:.0f}s")
        return " · ".join(parts)


def is_video(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in VIDEO_EXTENSIONS


def probe_video(path: str) -> Optional[Dict]:
    """Read resolution, codec, duration and bitrate with ffprobe."""
    cmd = [
        'ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height,codec_name,bit_rate:format=duration,bit_rate',
        '-of', 'json', path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout or '{}')
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        logger.debug(f"ffprobe failed for {path}: {e}")
        return None

    streams = data.get('streams') or []
    if not streams:
        return None
    stream, fmt = streams[0], data.get('format', {})

    def _num(value, cast):
        try:
            return cast(value)
        except (TypeError, ValueError):
            return cast(0)

    return {
        'width': _num(stream.get('width'), int),
        'height': _num(stream.get('height'), int),
        'codec': stream.get('codec_name') or '',
        'duration': _num(fmt.get('duration'), float),
        # Container bitrate covers webm, where the stream often has none
        'bitrate': _num(fmt.get('bit_rate'), int) or _num(stream.get('bit_rate'), int),
    }


def _crop_borders(frame: np.ndarray, threshold: int = 24, min_fill: float = 0.02) -> np.ndarray:
    """Strip near-black letterbox/pillarbox bars so they don't skew the hash."""
    mask = frame.max(axis=2) > threshold
    rows = np.where(mask.mean(axis=1) > min_fill)[0]
    cols = np.where(mask.mean(axis=0) > min_fill)[0]
    if len(rows) < 10 or len(cols) < 10:
        return frame  # Mostly dark frame - leave it alone
    return frame[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]


def _extract_frame_hashes(path: str, duration: float, count: int = FRAME_COUNT) -> List[int]:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return []

    if duration <= 0:
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        duration = frames / fps if fps else 0

    hashes = []
    try:
        for i in range(count):
            if duration > 0:
                cap.set(cv2.CAP_PROP_POS_MSEC, duration * 1000 * (i + 0.5) / count)
            ok, frame = cap.read()
            if not ok:
                continue
            frame = _crop_borders(frame)
            img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            hashes.append(int(str(imagehash.phash(img)), 16))
    finally:
        cap.release()

    # Pad to a fixed length so signatures stack into one matrix
    if hashes and len(hashes) < count:
        hashes = [hashes[i % len(hashes)] for i in range(count)]
    return hashes


def compute_signature(path: str) -> Optional[VideoSignature]:
    """Build a signature for one video, or None if it can't be read."""
    if not HAS_VIDEO_SIMILARITY:
        raise RuntimeError("Video similarity needs opencv-python, imagehash and Pillow")

    meta = probe_video(path) or {}
    hashes = _extract_frame_hashes(path, meta.get('duration', 0.0))
    if not hashes:
        logger.warning(f"Could not read frames from {path}")
        return None
    return VideoSignature(frame_hashes=hashes, **{
        k: meta.get(k, v) for k, v in
        (('width', 0), ('height', 0), ('codec', ''), ('duration', 0.0), ('bitrate', 0))
    })


def _popcount(values: np.ndarray) -> np.ndarray:
    if hasattr(np, 'bitwise_count'):
        return np.bitwise_count(values)
    as_bytes = values.view(np.uint8).reshape(values.shape + (8,))
    return _POPCOUNT8[as_bytes].sum(axis=-1, dtype=np.uint8)


def signature_distance(a: VideoSignature, b: VideoSignature) -> int:
    """Distance between two signatures (0 = identical, 64 = unrelated)."""
    ha = np.array(a.frame_hashes, dtype=np.uint64)
    hb = np.array(b.frame_hashes, dtype=np.uint64)
    d = _popcount(ha[:, None] ^ hb[None, :])
    a_to_b = int(np.sort(d.min(axis=1))[len(ha) // 2])
    b_to_a = int(np.sort(d.min(axis=0))[len(hb) // 2])
    return min(a_to_b, b_to_a)


def find_similar_pairs(signatures: List[VideoSignature],
                       max_distance: int = DEFAULT_MAX_DISTANCE,
                       stopped_flag: Optional[Callable[[], bool]] = None,
                       progress_callback: Optional[Callable[[int, int], None]] = None
                       ) -> List[Tuple[int, int, int]]:
    """Compare every pair of signatures; return (i, j, distance) for matches.

    Vectorised one row at a time: each video's frames are XORed against the
    frames of every later video in a single numpy operation.
    """
    n = len(signatures)
    if n < 2:
        return []

    frames = len(signatures[0].frame_hashes)
    matrix = np.array([s.frame_hashes for s in signatures], dtype=np.uint64)  # (n, frames)
    mid = frames // 2
    pairs = []

    for i in range(n - 1):
        if stopped_flag and stopped_flag():
            break
        others = matrix[i + 1:]                                     # (m, frames)
        d = _popcount(matrix[i][None, :, None] ^ others[:, None, :])  # (m, frames_i, frames_j)
        a_to_b = np.partition(d.min(axis=2), mid, axis=1)[:, mid]
        b_to_a = np.partition(d.min(axis=1), mid, axis=1)[:, mid]
        dist = np.minimum(a_to_b, b_to_a)
        for offset in np.nonzero(dist <= max_distance)[0]:
            pairs.append((i, i + 1 + int(offset), int(dist[offset])))
        if progress_callback and i % 25 == 0:
            progress_callback(i, n)

    return pairs


def group_pairs(count: int, pairs: Iterable[Tuple[int, int, int]]) -> List[Tuple[List[int], int]]:
    """Union matching pairs into groups; returns (member indices, worst pair distance)."""
    parent = list(range(count))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    worst: Dict[int, int] = {}
    pairs = list(pairs)
    for i, j, _ in pairs:
        parent[find(i)] = find(j)
    for i, _, d in pairs:
        root = find(i)
        worst[root] = max(worst.get(root, 0), d)

    members: Dict[int, List[int]] = {}
    for idx in range(count):
        members.setdefault(find(idx), []).append(idx)
    return [(m, worst.get(root, 0)) for root, m in members.items() if len(m) > 1]


class VideoSignatureCache:
    """Signatures stored in the sorter DB, keyed by path and invalidated on size/mtime change."""

    TABLE = 'video_signatures'

    def __init__(self, get_connection: Callable, database: str):
        self._get_connection = get_connection
        self._table = f"`{database}`.`{self.TABLE}`"
        self._ensure_table()

    @staticmethod
    def _key(path: str) -> str:
        return hashlib.md5(os.path.normcase(os.path.normpath(path)).encode('utf-8')).hexdigest()

    def _ensure_table(self):
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._table} (
                    `path_key` CHAR(32) PRIMARY KEY,
                    `file_path` TEXT NOT NULL,
                    `file_size` BIGINT NOT NULL,
                    `file_mtime` DOUBLE NOT NULL,
                    `version` INT NOT NULL,
                    `width` INT NOT NULL DEFAULT 0,
                    `height` INT NOT NULL DEFAULT 0,
                    `codec` VARCHAR(32) NOT NULL DEFAULT '',
                    `duration` DOUBLE NOT NULL DEFAULT 0,
                    `bitrate` BIGINT NOT NULL DEFAULT 0,
                    `frame_hashes` TEXT NOT NULL,
                    `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            conn.commit()
            cursor.close()
        finally:
            conn.close()

    def load(self, paths: Iterable[str]) -> Dict[str, VideoSignature]:
        """Return cached signatures still valid for the files on disk."""
        wanted = {}
        for path in paths:
            try:
                st = os.stat(path)
            except OSError:
                continue
            wanted[self._key(path)] = (path, st.st_size, st.st_mtime)
        if not wanted:
            return {}

        found = {}
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            keys = list(wanted)
            for start in range(0, len(keys), 500):
                chunk = keys[start:start + 500]
                cursor.execute(
                    f"SELECT path_key, file_size, file_mtime, version, width, height, codec, "
                    f"duration, bitrate, frame_hashes FROM {self._table} "
                    f"WHERE path_key IN ({', '.join(['%s'] * len(chunk))})",
                    chunk,
                )
                for key, size, mtime, version, w, h, codec, dur, br, hashes in cursor.fetchall():
                    path, cur_size, cur_mtime = wanted[key]
                    if version != SIGNATURE_VERSION or size != cur_size or abs(mtime - cur_mtime) > 1:
                        continue
                    found[path] = VideoSignature(
                        width=w, height=h, codec=codec, duration=dur, bitrate=br,
                        frame_hashes=[int(x, 16) for x in hashes.split(',') if x],
                    )
            cursor.close()
        finally:
            conn.close()
        return found

    def save(self, items: Dict[str, VideoSignature]):
        if not items:
            return
        rows = []
        for path, sig in items.items():
            try:
                st = os.stat(path)
            except OSError:
                continue
            rows.append((
                self._key(path), path, st.st_size, st.st_mtime, SIGNATURE_VERSION,
                sig.width, sig.height, sig.codec[:32], sig.duration, sig.bitrate,
                ','.join(f"{h:016x}" for h in sig.frame_hashes),
            ))
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.executemany(f"""
                INSERT INTO {self._table}
                    (path_key, file_path, file_size, file_mtime, version, width, height,
                     codec, duration, bitrate, frame_hashes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    file_path=VALUES(file_path), file_size=VALUES(file_size),
                    file_mtime=VALUES(file_mtime), version=VALUES(version),
                    width=VALUES(width), height=VALUES(height), codec=VALUES(codec),
                    duration=VALUES(duration), bitrate=VALUES(bitrate),
                    frame_hashes=VALUES(frame_hashes)
            """, rows)
            conn.commit()
            cursor.close()
        finally:
            conn.close()


def build_signatures(paths: List[str],
                     cache: Optional[VideoSignatureCache] = None,
                     workers: int = 4,
                     stopped_flag: Optional[Callable[[], bool]] = None,
                     progress_callback: Optional[Callable[[int, int, int], None]] = None
                     ) -> Dict[str, VideoSignature]:
    """Signatures for every path, reusing the cache and computing the rest in parallel.

    progress_callback receives (done, total, cached_count). New signatures are
    saved in batches so an interrupted run keeps its progress.
    """
    signatures = cache.load(paths) if cache else {}
    cached = len(signatures)
    todo = [p for p in paths if p not in signatures]
    total = len(paths)
    if progress_callback:
        progress_callback(cached, total, cached)

    pending: Dict[str, VideoSignature] = {}
    done = cached
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(compute_signature, p): p for p in todo}
        for future in as_completed(futures):
            path = futures[future]
            done += 1
            try:
                sig = future.result()
            except Exception as e:
                logger.warning(f"Signature failed for {path}: {e}")
                sig = None
            if sig:
                signatures[path] = sig
                pending[path] = sig
            if cache and len(pending) >= 25:
                cache.save(pending)
                pending = {}
            if progress_callback:
                progress_callback(done, total, cached)
            if stopped_flag and stopped_flag():
                for f in futures:
                    f.cancel()
                break

    if cache and pending:
        cache.save(pending)
    return signatures
