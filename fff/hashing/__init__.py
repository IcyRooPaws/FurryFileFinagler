"""One media hasher, two profiles (design note section5).

The sorter and the archive both write perceptual hashes into existing DB columns, and
their existing code produced different values for the same file:

* ``sorter_hasher()``: media sorter's behaviour. Settings come from the sorter config's
  ``media_hasher`` section, compute_all_hashes defaults on (``avg|dhash|phash|whash``),
  and images are normalized before hashing.
* ``archive_hasher()``: e621 archive's behaviour. Built-in defaults only (its config
  block was never read), mode-based output (``dhash|phash`` for hybrid), no normalization.

tests/test_hashing.py checks both against the original modules.
"""
from fff.hashing.media_hasher import (  # noqa: F401  (re-exported)
    ContentHasher,
    FastHasher,
    HighSensitivityHasher,
    MediaHasher,
    PerceptualHasher,
    are_images_similar,
    compare_perceptual_hashes,
    get_file_info,
    get_similarity_score,
    hash_file,
)


__all__ = ["ContentHasher", "FastHasher", "HighSensitivityHasher", "MediaHasher", "PerceptualHasher",
           "are_images_similar", "compare_perceptual_hashes", "get_file_info", "get_similarity_score",
           "hash_file", "sorter_hasher", "archive_hasher"]


def sorter_hasher(**overrides) -> MediaHasher:
    return MediaHasher(**overrides)


def archive_hasher(**overrides) -> MediaHasher:
    kwargs = {"use_config": False, "compute_all_hashes": False, "normalize_images": False}
    kwargs.update(overrides)
    return MediaHasher(**kwargs)
