# fff: implemented in media sorter/_handlers/media_hasher/core.py.
# fff: old v1 hasher package, kept verbatim: DatabaseManager._get_hash_metadata uses its type
# fff: mapping for the hash_method/hash_type columns, and 'fff hash' wraps its CLI.
"""
MediaHash Library - Multi-format file hashing with type-specific algorithms

A flexible hashing library that provides different hashing strategies based on file types:
- Perceptual hashing for images (visual similarity detection)
- Content-based hashing for audio files (metadata and header analysis)
- Smart sampling for other file types (efficient for large files)

Author: Created for cross-project media file management
License: MIT
"""

from pathlib import Path
import hashlib
import logging
from typing import Optional, Dict, Any
import mimetypes
import warnings

# Optional dependencies - gracefully handle missing packages
try:
    from PIL import Image
    import imagehash

    # Configure PIL to handle large images and suppress decompression warnings
    Image.MAX_IMAGE_PIXELS = None  # Remove default limit
    warnings.filterwarnings('ignore', category=Image.DecompressionBombWarning)

    HAS_IMAGE_SUPPORT = True
except ImportError:
    HAS_IMAGE_SUPPORT = False


class MediaHasher:
    """
    Multi-format file hasher with type-specific algorithms

    Provides different hashing strategies optimized for different file types:
    - Images: Perceptual hashing for similarity detection
    - Audio: Content-based hashing of headers/metadata
    - Other: Smart sampling for efficient processing
    """

    # File extension mappings
    IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff', '.tif'}
    AUDIO_EXTENSIONS = {'.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aac', '.wma', '.opus'}
    VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v'}

    def __init__(self,
                 hash_length: int = 16,
                 audio_sample_size: int = 1048576,  # 1MB
                 large_file_threshold: int = 1048576,  # 1MB
                 content_sample_size: int = 1048576):  # 1MB
        """
        Initialize the MediaHasher

        Args:
            hash_length: Length of returned hash strings (default: 16)
            audio_sample_size: Bytes to read from audio files (default: 64KB)
            large_file_threshold: Size threshold for smart sampling (default: 1MB)
            content_sample_size: Sample size for large file chunks (default: 32KB)
        """
        self.hash_length = hash_length
        self.audio_sample_size = audio_sample_size
        self.large_file_threshold = large_file_threshold
        self.content_sample_size = content_sample_size

        # Set up logging
        self.logger = logging.getLogger(__name__)

    def hash_file(self, file_path: str | Path) -> Optional[str]:
        """
        Hash a file using the appropriate algorithm based on file type

        Args:
            file_path: Path to the file to hash

        Returns:
            Hash string or None if hashing failed
        """
        try:
            path_obj = Path(file_path)

            if not path_obj.exists():
                self.logger.warning(f"File does not exist: {file_path}")
                return None

            if not path_obj.is_file():
                self.logger.warning(f"Path is not a file: {file_path}")
                return None

            file_ext = path_obj.suffix.lower()
            file_type = self._get_file_type(file_ext)

            # Route to appropriate hashing method
            if file_type == 'image':
                return self._hash_image(path_obj)
            elif file_type == 'audio':
                return self._hash_audio(path_obj)
            else:
                return self._hash_content(path_obj)

        except Exception as e:
            self.logger.error(f"Error hashing file {file_path}: {e}")
            return None

    def _get_file_type(self, extension: str) -> str:
        """Determine file type category from extension"""
        if extension in self.IMAGE_EXTENSIONS:
            return 'image'
        elif extension in self.AUDIO_EXTENSIONS:
            return 'audio'
        elif extension in self.VIDEO_EXTENSIONS:
            return 'video'
        else:
            return 'other'

    def _hash_image(self, file_path: Path) -> Optional[str]:
        """
        Hash image files using perceptual hashing

        Perceptual hashing creates hashes based on visual content, allowing
        detection of similar images even after resize, crop, or minor modifications.
        """
        if not HAS_IMAGE_SUPPORT:
            self.logger.warning("PIL/imagehash not available, falling back to content hash")
            return self._hash_content(file_path)

        try:
            # Temporarily suppress PIL warnings for this operation
            with warnings.catch_warnings():
                warnings.filterwarnings('ignore', category=Image.DecompressionBombWarning)
                warnings.filterwarnings('ignore', message='.*Palette images with transparency.*')

                # Configure PIL to handle truncated/incomplete images
                from PIL import ImageFile
                ImageFile.LOAD_TRUNCATED_IMAGES = True

                with Image.open(file_path) as img:
                    # Force load the image data to catch truncation issues early
                    img.load()
                    
                    # Use average hash on original image - no resizing
                    # Perceptual hashing works well even on large images
                    img_hash = imagehash.average_hash(img)
                    return str(img_hash)[:self.hash_length]

        except (OSError, IOError, Image.UnidentifiedImageError) as e:
            # Handle truncated, corrupted, or unreadable image files
            if "truncated" in str(e).lower():
                self.logger.warning(f"Image file truncated (corrupted/incomplete): {file_path}")
            elif "cannot identify image file" in str(e).lower():
                self.logger.warning(f"Unidentified image format: {file_path}")
            else:
                self.logger.warning(f"Image file error: {file_path} - {e}")
            # Fallback to content hashing for corrupted images
            return self._hash_content(file_path)
        except Exception as e:
            self.logger.warning(f"Could not create perceptual hash for {file_path}: {e}")
            # Fallback to content hashing
            return self._hash_content(file_path)

    def _hash_audio(self, file_path: Path) -> Optional[str]:
        """
        Hash audio files using content-based hashing of file headers

        Reads the beginning of audio files to capture metadata, format headers,
        and initial audio data for unique identification.
        """
        try:
            with open(file_path, 'rb') as f:
                # Read header portion which contains metadata and format info
                chunk = f.read(self.audio_sample_size)

                # Handle empty files or files smaller than sample size
                hash_obj = hashlib.sha256(chunk)
                return hash_obj.hexdigest()[:self.hash_length]

        except Exception as e:
            self.logger.warning(f"Could not hash audio file {file_path}: {e}")
            return None

    def _hash_content(self, file_path: Path) -> Optional[str]:
        """
        Hash other files using smart content sampling

        For small files: reads entire content
        For large files: samples from beginning, middle, and end
        """
        try:
            file_size = file_path.stat().st_size

            with open(file_path, 'rb') as f:
                if file_size <= self.large_file_threshold:
                    # Small file - read everything
                    content = f.read()
                else:
                    # Large file - strategic sampling
                    chunks = []

                    # Beginning
                    chunks.append(f.read(self.content_sample_size))

                    # Middle
                    middle_pos = file_size // 2
                    f.seek(middle_pos)
                    chunks.append(f.read(self.content_sample_size))

                    # End
                    end_pos = max(0, file_size - self.content_sample_size)
                    f.seek(end_pos)
                    chunks.append(f.read(self.content_sample_size))

                    content = b''.join(chunks)

                # Handle empty files - hash empty content
                hash_obj = hashlib.sha256(content)
                return hash_obj.hexdigest()[:self.hash_length]

        except Exception as e:
            self.logger.warning(f"Could not hash content of {file_path}: {e}")
            return None

    def get_file_info(self, file_path: str | Path) -> Dict[str, Any]:
        """
        Get comprehensive information about a file including hash

        Returns:
            Dictionary with file information and hash
        """
        path_obj = Path(file_path)

        try:
            stat = path_obj.stat()
            file_ext = path_obj.suffix.lower()
            file_type = self._get_file_type(file_ext)

            # Determine MIME type
            mime_type, _ = mimetypes.guess_type(str(path_obj))

            info = {
                'path': str(path_obj.absolute()),
                'name': path_obj.name,
                'extension': file_ext,
                'file_type': file_type,
                'mime_type': mime_type,
                'size': stat.st_size,
                'modified': stat.st_mtime,
                'hash': self.hash_file(path_obj),
                'hash_method': self._get_hash_method(file_type)
            }

            return info

        except Exception as e:
            self.logger.error(f"Error getting file info for {file_path}: {e}")
            return {
                'path': str(path_obj.absolute()),
                'name': path_obj.name,
                'error': str(e),
                'hash': None
            }

    def _get_hash_method(self, file_type: str) -> str:
        """Return the hashing method used for a given file type"""
        if file_type == 'image' and HAS_IMAGE_SUPPORT:
            return 'perceptual'
        elif file_type == 'audio':
            return 'audio_content'
        else:
            return 'smart_content'

    def batch_hash(self, file_paths: list) -> Dict[str, Optional[str]]:
        """
        Hash multiple files in batch

        Args:
            file_paths: List of file paths to hash

        Returns:
            Dictionary mapping file paths to their hashes
        """
        results = {}

        for file_path in file_paths:
            results[str(file_path)] = self.hash_file(file_path)

        return results


# Convenience functions for simple usage
def hash_file(file_path: str | Path, hash_length: int = 16) -> Optional[str]:
    """
    Simple function to hash a single file

    Args:
        file_path: Path to file
        hash_length: Length of returned hash

    Returns:
        Hash string or None
    """
    hasher = MediaHasher(hash_length=hash_length)
    return hasher.hash_file(file_path)


def get_file_info(file_path: str | Path) -> Dict[str, Any]:
    """
    Get comprehensive file information including hash

    Args:
        file_path: Path to file

    Returns:
        Dictionary with file information
    """
    hasher = MediaHasher()
    return hasher.get_file_info(file_path)


# Support for different hash algorithms
class PerceptualHasher(MediaHasher):
    """Specialized hasher that uses perceptual hashing for all images"""

    def _hash_image(self, file_path: Path) -> Optional[str]:
        if not HAS_IMAGE_SUPPORT:
            raise ImportError("PIL and imagehash required for perceptual hashing")
        return super()._hash_image(file_path)


class ContentHasher(MediaHasher):
    """Hasher that uses content hashing for all file types"""

    def hash_file(self, file_path: str | Path) -> Optional[str]:
        try:
            path_obj = Path(file_path)
            if not path_obj.exists():
                return None
            return self._hash_content(path_obj)
        except Exception as e:
            self.logger.error(f"Error hashing file {file_path}: {e}")
            return None
