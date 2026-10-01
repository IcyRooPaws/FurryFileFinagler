# fff: implemented in media sorter/_handlers/media_hasher/__init__.py.
# fff: old v1 hasher package, kept verbatim: DatabaseManager._get_hash_metadata uses its type
# fff: mapping for the hash_method/hash_type columns, and 'fff hash' wraps its CLI.
"""
MediaHasher - Multi-format file hashing with type-specific algorithms

A flexible hashing library that provides different hashing strategies based on file types:
- Perceptual hashing for images (visual similarity detection)
- Content-based hashing for audio files (metadata and header analysis)
- Smart sampling for other file types (efficient for large files)

Quick Start:
    >>> from media_hasher import hash_file, MediaHasher
    >>> hash_file("image.jpg")
    'a1b2c3d4e5f6g7h8'

    >>> hasher = MediaHasher(hash_length=32)
    >>> hasher.hash_file("document.pdf")
    'a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6'

Author: Created for cross-project media file management
License: MIT
Version: 1.0.0
"""

from .core import (
    MediaHasher,
    PerceptualHasher,
    ContentHasher,
    hash_file,
    get_file_info,
    HAS_IMAGE_SUPPORT
)

from .utils import (
    batch_hash_directory,
    find_duplicates,
    compare_files,
    supported_extensions
)

__version__ = "1.0.0"
__author__ = "MediaHasher Project"
__license__ = "MIT"

# Main exports for easy importing
__all__ = [
    # Core classes
    'MediaHasher',
    'PerceptualHasher',
    'ContentHasher',

    # Convenience functions
    'hash_file',
    'get_file_info',
    'batch_hash_directory',
    'find_duplicates',
    'compare_files',
    'supported_extensions',

    # Constants
    'HAS_IMAGE_SUPPORT',
    '__version__'
]

# Backwards compatibility - allow importing main class directly from package
def create_hasher(*args, **kwargs):
    """Factory function to create a MediaHasher instance"""
    return MediaHasher(*args, **kwargs)
