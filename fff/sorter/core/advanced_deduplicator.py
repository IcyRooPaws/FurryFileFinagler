"""
Advanced Deduplicator - Multi-stage duplicate detection for FFF

Integrates the deduplicator's efficient multi-stage pipeline with FFF's
database management and media-specific hashing capabilities.

Features:
- Multi-stage hashing: size → front → middle → end → full
- xxhash for speed
- Optional perceptual hashing for images
- Database-backed results
- Progress tracking
"""

import xxhash
import logging
from typing import List, Dict, Tuple, Optional, Callable, Set
from pathlib import Path
from dataclasses import dataclass, field
from collections import defaultdict
from enum import Enum
import time

logger = logging.getLogger(__name__)

# Optional image similarity support
try:
    from PIL import Image
    import imagehash
    HAS_IMAGE_SUPPORT = True
except ImportError:
    HAS_IMAGE_SUPPORT = False


class DedupeMode(Enum):
    """Deduplication strategies"""
    FAST = "fast"          # size + front hash
    NORMAL = "normal"      # size + front + middle + end
    FULL = "full"          # size + front + middle + full hash
    PERCEPTUAL = "perceptual"  # Use perceptual hashing for images (requires PIL)


@dataclass
class FileHashes:
    """Container for cached file hashes"""
    front: Optional[bytes] = None
    middle: Optional[bytes] = None
    end: Optional[bytes] = None
    full: Optional[bytes] = None
    perceptual: Optional[str] = None  # For image similarity


@dataclass
class DuplicateFile:
    """File representation for duplicate detection"""
    path: str
    size: int
    table_name: Optional[str] = None  # FFF database table
    existing_hash: Optional[str] = None  # From database
    mtime: Optional[int] = None
    is_favorite: bool = False  # From favorite directory
    hashes: FileHashes = field(default_factory=FileHashes)

    def __hash__(self):
        return hash(self.path)


@dataclass
class DuplicateGroup:
    """Group of duplicate files"""
    size: int
    files: List[DuplicateFile]
    detection_stage: str = "unknown"  # Which stage confirmed the duplicate

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def total_size(self) -> int:
        """Total disk space used by duplicates (excluding first file)"""
        return self.size * max(0, len(self.files) - 1)


@dataclass
class DedupeStats:
    """Statistics from deduplication run"""
    total_files: int = 0
    duplicate_groups: int = 0
    duplicate_files: int = 0
    wasted_space: int = 0
    stages_run: List[str] = field(default_factory=list)
    stage_times: Dict[str, float] = field(default_factory=dict)
    total_time: float = 0.0

    def __str__(self):
        lines = [
            f"=== Deduplication Statistics ===",
            f"Total files scanned: {self.total_files:,}",
            f"Duplicate groups found: {self.duplicate_groups:,}",
            f"Duplicate files: {self.duplicate_files:,}",
            f"Wasted space: {self._format_bytes(self.wasted_space)}",
            f"Total time: {self.total_time:.2f}s",
            f"\nStage execution times:"
        ]
        for stage in self.stages_run:
            time_taken = self.stage_times.get(stage, 0)
            lines.append(f"  {stage}: {time_taken:.2f}s")
        return "\n".join(lines)

    @staticmethod
    def _format_bytes(size: int) -> str:
        """Format bytes to human-readable string"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} PB"


class AdvancedDeduplicator:
    """
    Multi-stage duplicate detector optimized for large file collections.

    Uses a progressive filtering approach:
    1. Group by size (fast, no I/O)
    2. Hash first chunk (eliminates most non-duplicates)
    3. Hash middle/end chunks (confirms true duplicates)
    4. Full hash (for final verification if needed)
    """

    # Default chunk sizes for partial hashing
    CHUNK_SIZE = 64 * 1024  # 64KB
    AUDIO_CHUNK_SIZE = 128 * 1024  # 128KB for audio files

    def __init__(self,
                 chunk_size: int = CHUNK_SIZE,
                 use_database: bool = True):
        """
        Initialize the deduplicator.

        Args:
            chunk_size: Size of chunks for partial hashing
            use_database: Whether to integrate with FFF database
        """
        self.chunk_size = chunk_size
        self.use_database = use_database
        self.stats = DedupeStats()

    def find_duplicates(self,
                       files: List[DuplicateFile],
                       mode: DedupeMode = DedupeMode.NORMAL,
                       image_extensions: Optional[Set[str]] = None,
                       stopped_flag: Optional[Callable[[], bool]] = None,
                       progress_callback: Optional[Callable[[str, int, int], None]] = None) -> Tuple[List[DuplicateGroup], DedupeStats]:
        """
        Find duplicates using multi-stage pipeline.

        Args:
            files: List of files to check
            mode: Deduplication strategy
            image_extensions: Extensions to treat as images for perceptual hashing
            stopped_flag: Function that returns True to cancel operation
            progress_callback: Called with (stage_name, current, total)

        Returns:
            Tuple of (duplicate_groups, statistics)
        """
        start_time = time.time()
        self.stats = DedupeStats(total_files=len(files))

        if not files:
            return [], self.stats

        # Stage 1: Group by size
        groups = self._group_by_size(files, stopped_flag, progress_callback)
        self._update_stats("size", time.time() - start_time)

        if not groups:
            self.stats.total_time = time.time() - start_time
            return [], self.stats

        # Build pipeline based on mode
        confirmed = []

        if mode == DedupeMode.PERCEPTUAL and HAS_IMAGE_SUPPORT:
            # Use perceptual hashing for images
            stage_start = time.time()
            confirmed, groups = self._perceptual_stage(
                groups, confirmed, image_extensions or {'.jpg', '.jpeg', '.png', '.webp'},
                stopped_flag, progress_callback
            )
            self._update_stats("perceptual", time.time() - stage_start)
        else:
            # Standard multi-stage pipeline
            if mode in [DedupeMode.FAST, DedupeMode.NORMAL, DedupeMode.FULL]:
                stage_start = time.time()
                confirmed, groups = self._front_hash_stage(groups, confirmed, stopped_flag, progress_callback)
                self._update_stats("front", time.time() - stage_start)

            if mode in [DedupeMode.NORMAL, DedupeMode.FULL] and groups:
                stage_start = time.time()
                confirmed, groups = self._middle_hash_stage(groups, confirmed, stopped_flag, progress_callback)
                self._update_stats("middle", time.time() - stage_start)

                stage_start = time.time()
                confirmed, groups = self._end_hash_stage(groups, confirmed, stopped_flag, progress_callback)
                self._update_stats("end", time.time() - stage_start)

            if mode == DedupeMode.FULL and groups:
                stage_start = time.time()
                confirmed, groups = self._full_hash_stage(groups, confirmed, stopped_flag, progress_callback)
                self._update_stats("full", time.time() - stage_start)

        # Combine all confirmed duplicates
        all_duplicates = confirmed + groups
        all_duplicates.sort(key=lambda g: -g.size)  # Sort by size descending

        # Calculate final stats
        self.stats.duplicate_groups = len(all_duplicates)
        self.stats.duplicate_files = sum(g.file_count for g in all_duplicates)
        self.stats.wasted_space = sum(g.total_size for g in all_duplicates)
        self.stats.total_time = time.time() - start_time

        return all_duplicates, self.stats

    def _group_by_size(self, files: List[DuplicateFile],
                       stopped_flag: Optional[Callable[[], bool]],
                       progress_callback: Optional[Callable[[str, int, int], None]]) -> List[DuplicateGroup]:
        """Stage 1: Group files by size"""
        size_groups = defaultdict(list)

        for i, file in enumerate(files):
            if stopped_flag and stopped_flag():
                return []

            if progress_callback and i % 100 == 0:
                progress_callback("Grouping by size", i, len(files))

            size_groups[file.size].append(file)

        # Only keep groups with duplicates
        groups = [
            DuplicateGroup(size=size, files=file_list, detection_stage="size")
            for size, file_list in size_groups.items()
            if len(file_list) > 1
        ]

        logger.info(f"Size grouping: {len(groups)} potential duplicate groups from {len(files)} files")
        return groups

    def _front_hash_stage(self, groups: List[DuplicateGroup], confirmed: List[DuplicateGroup],
                          stopped_flag: Optional[Callable[[], bool]],
                          progress_callback: Optional[Callable[[str, int, int], None]]) -> Tuple[List[DuplicateGroup], List[DuplicateGroup]]:
        """Stage 2: Hash first chunk of files"""
        return self._hash_stage(groups, confirmed, "front", self._compute_front_hash, stopped_flag, progress_callback)

    def _middle_hash_stage(self, groups: List[DuplicateGroup], confirmed: List[DuplicateGroup],
                           stopped_flag: Optional[Callable[[], bool]],
                           progress_callback: Optional[Callable[[str, int, int], None]]) -> Tuple[List[DuplicateGroup], List[DuplicateGroup]]:
        """Stage 3: Hash middle chunk of files"""
        return self._hash_stage(groups, confirmed, "middle", self._compute_middle_hash, stopped_flag, progress_callback)

    def _end_hash_stage(self, groups: List[DuplicateGroup], confirmed: List[DuplicateGroup],
                        stopped_flag: Optional[Callable[[], bool]],
                        progress_callback: Optional[Callable[[str, int, int], None]]) -> Tuple[List[DuplicateGroup], List[DuplicateGroup]]:
        """Stage 4: Hash end chunk of files"""
        return self._hash_stage(groups, confirmed, "end", self._compute_end_hash, stopped_flag, progress_callback)

    def _full_hash_stage(self, groups: List[DuplicateGroup], confirmed: List[DuplicateGroup],
                         stopped_flag: Optional[Callable[[], bool]],
                         progress_callback: Optional[Callable[[str, int, int], None]]) -> Tuple[List[DuplicateGroup], List[DuplicateGroup]]:
        """Stage 5: Hash full file content"""
        return self._hash_stage(groups, confirmed, "full", self._compute_full_hash, stopped_flag, progress_callback)

    def _hash_stage(self, groups: List[DuplicateGroup], confirmed: List[DuplicateGroup],
                    stage_name: str, hash_func: Callable,
                    stopped_flag: Optional[Callable[[], bool]],
                    progress_callback: Optional[Callable[[str, int, int], None]]) -> Tuple[List[DuplicateGroup], List[DuplicateGroup]]:
        """
        Generic hash stage that refines groups by computing hashes.

        Returns:
            (confirmed_groups, remaining_groups_to_process)
        """
        new_groups = []
        total_files = sum(len(g.files) for g in groups)
        processed = 0

        for group in groups:
            if stopped_flag and stopped_flag():
                break

            # Compute hashes for all files in group
            hash_map = defaultdict(list)
            for file in group.files:
                if stopped_flag and stopped_flag():
                    break

                file_hash = hash_func(file)
                if file_hash:
                    hash_map[file_hash].append(file)

                processed += 1
                if progress_callback and processed % 10 == 0:
                    progress_callback(f"Hashing {stage_name}", processed, total_files)

            # Split group by hash values
            for file_list in hash_map.values():
                if len(file_list) > 1:
                    # Still has duplicates - confirm or continue processing
                    new_group = DuplicateGroup(
                        size=group.size,
                        files=file_list,
                        detection_stage=stage_name
                    )

                    # If this is end stage or full stage, confirm as duplicate
                    if stage_name in ["end", "full"]:
                        confirmed.append(new_group)
                    else:
                        new_groups.append(new_group)

        logger.info(f"{stage_name.capitalize()} hash: {len(new_groups)} groups remaining, {len(confirmed)} confirmed")
        return confirmed, new_groups

    def _perceptual_stage(self, groups: List[DuplicateGroup], confirmed: List[DuplicateGroup],
                          image_extensions: Set[str],
                          stopped_flag: Optional[Callable[[], bool]],
                          progress_callback: Optional[Callable[[str, int, int], None]]) -> Tuple[List[DuplicateGroup], List[DuplicateGroup]]:
        """Use perceptual hashing for image similarity detection"""
        if not HAS_IMAGE_SUPPORT:
            logger.warning("PIL not available, falling back to standard hashing")
            return self._front_hash_stage(groups, confirmed, stopped_flag, progress_callback)

        new_groups = []
        total_files = sum(len(g.files) for g in groups)
        processed = 0

        for group in groups:
            if stopped_flag and stopped_flag():
                break

            # Separate images from non-images
            images = []
            non_images = []

            for file in group.files:
                ext = Path(file.path).suffix.lower()
                if ext in image_extensions:
                    images.append(file)
                else:
                    non_images.append(file)

            # Process images with perceptual hashing
            if len(images) > 1:
                hash_map = defaultdict(list)
                for file in images:
                    if stopped_flag and stopped_flag():
                        break

                    phash = self._compute_perceptual_hash(file)
                    if phash:
                        hash_map[phash].append(file)

                    processed += 1
                    if progress_callback and processed % 10 == 0:
                        progress_callback("Perceptual hashing", processed, total_files)

                # Groups with same perceptual hash are duplicates
                for file_list in hash_map.values():
                    if len(file_list) > 1:
                        confirmed.append(DuplicateGroup(
                            size=group.size,
                            files=file_list,
                            detection_stage="perceptual"
                        ))

            # Process non-images with standard hashing
            if len(non_images) > 1:
                new_groups.append(DuplicateGroup(
                    size=group.size,
                    files=non_images,
                    detection_stage="size"
                ))

        return confirmed, new_groups

    def _compute_front_hash(self, file: DuplicateFile) -> Optional[bytes]:
        """Compute hash of first chunk"""
        if file.hashes.front:
            return file.hashes.front

        data = self._read_chunk(file.path, 0, self.chunk_size)
        if data:
            file.hashes.front = xxhash.xxh64(data).digest()
        return file.hashes.front

    def _compute_middle_hash(self, file: DuplicateFile) -> Optional[bytes]:
        """Compute hash of middle chunk"""
        if file.hashes.middle:
            return file.hashes.middle

        offset = max(0, file.size // 2 - self.chunk_size // 2)
        data = self._read_chunk(file.path, offset, self.chunk_size)
        if data:
            file.hashes.middle = xxhash.xxh64(data).digest()
        return file.hashes.middle

    def _compute_end_hash(self, file: DuplicateFile) -> Optional[bytes]:
        """Compute hash of last chunk"""
        if file.hashes.end:
            return file.hashes.end

        offset = max(0, file.size - self.chunk_size)
        data = self._read_chunk(file.path, offset, self.chunk_size)
        if data:
            file.hashes.end = xxhash.xxh64(data).digest()
        return file.hashes.end

    def _compute_full_hash(self, file: DuplicateFile) -> Optional[bytes]:
        """Compute hash of entire file"""
        if file.hashes.full:
            return file.hashes.full

        try:
            with open(file.path, 'rb') as f:
                hasher = xxhash.xxh64()
                while chunk := f.read(self.chunk_size):
                    hasher.update(chunk)
                file.hashes.full = hasher.digest()
            return file.hashes.full
        except Exception as e:
            logger.error(f"Failed to hash {file.path}: {e}")
            return None

    def _compute_perceptual_hash(self, file: DuplicateFile) -> Optional[str]:
        """Compute perceptual hash for images"""
        if file.hashes.perceptual:
            return file.hashes.perceptual

        if not HAS_IMAGE_SUPPORT:
            return None

        try:
            with Image.open(file.path) as img:
                # Use average hash for good balance of speed and accuracy
                phash = str(imagehash.average_hash(img))
                file.hashes.perceptual = phash
                return phash
        except Exception as e:
            logger.debug(f"Failed to compute perceptual hash for {file.path}: {e}")
            return None

    @staticmethod
    def _read_chunk(path: str, offset: int, size: int) -> Optional[bytes]:
        """Read a chunk of data from file"""
        try:
            with open(path, 'rb') as f:
                f.seek(offset)
                return f.read(size)
        except Exception as e:
            logger.error(f"Failed to read {path} at offset {offset}: {e}")
            return None

    def _update_stats(self, stage: str, duration: float):
        """Update statistics for a stage"""
        self.stats.stages_run.append(stage)
        self.stats.stage_times[stage] = duration
