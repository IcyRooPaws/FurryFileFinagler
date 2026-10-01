# Changes are marked "fff:": profile-based config lookup, `settings` and `normalize_images`.
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

# Optional dependencies - gracefully handle missing packages
try:
    from PIL import Image
    import imagehash
    HAS_IMAGE_SUPPORT = True

    # Disable decompression bomb warnings for large images
    # We're intentionally processing large images and want to hash them
    Image.MAX_IMAGE_PIXELS = None
except ImportError:
    HAS_IMAGE_SUPPORT = False

try:
    import cv2
    HAS_VIDEO_SUPPORT = True
except ImportError:
    HAS_VIDEO_SUPPORT = False

try:
    import yaml
    HAS_YAML_SUPPORT = True
except ImportError:
    HAS_YAML_SUPPORT = False


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

    @classmethod
    def _load_config_from_yaml(cls, config_path: Optional[Path] = None) -> Dict[str, Any]:
        """Load MediaHasher configuration from config.yaml file"""
        if not HAS_YAML_SUPPORT:
            return {}

        if config_path is None:
            # fff: the original searched upward from the CWD and, when run from FFF,
            # found configs/config.yaml. That media_hasher section is now the sorter's.
            try:
                from fff.common import config as fff_config
                return dict(fff_config.legacy_view("sorter").get("media_hasher") or {})
            except Exception:
                return {}

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                return config.get('media_hasher', {}) if config else {}
        except Exception:
            return {}

    def __init__(self,
                 hash_length: Optional[int] = None,
                 audio_sample_size: Optional[int] = None,
                 large_file_threshold: Optional[int] = None,
                 content_sample_size: Optional[int] = None,
                 image_hash_mode: Optional[str] = None,
                 image_sensitivity: Optional[str] = None,
                 compute_all_hashes: Optional[bool] = None,
                 config_path: Optional[Path] = None,
                 use_config: bool = True,
                 normalize_images: Optional[bool] = None,
                 settings: Optional[Dict[str, Any]] = None):
        """
        Initialize the MediaHasher

        Args:
            hash_length: Length of returned hash strings (default: 16)
            audio_sample_size: Bytes to read from audio files (default: 64KB)
            large_file_threshold: Size threshold for smart sampling (default: 1MB)
            content_sample_size: Sample size for large file chunks (default: 32KB)
            image_hash_mode: Hashing algorithm for images (default: 'hybrid')
                - 'average': Fast, good for basic similarity detection
                - 'dhash': Better for detecting minor differences
                - 'phash': More robust against transformations
                - 'whash': Wavelet-based, good for textured images
                - 'hybrid': Combines multiple algorithms for better accuracy
                - 'multi': Uses all algorithms and creates composite hash
            image_sensitivity: Sensitivity level for image differentiation (default: 'medium')
                - 'low': More likely to group similar images together
                - 'medium': Balanced approach
                - 'high': Better at detecting small differences
            compute_all_hashes: Always compute all hash types (dhash, phash, avg, whash) regardless of mode (default: True)
                - True: Populates all database columns for maximum flexibility
                - False: Only computes hashes specified by image_hash_mode
            config_path: Path to config.yaml file (default: auto-detect)
            use_config: Whether to load settings from config.yaml (default: True)
            normalize_images: fff addition. Pre-normalize images/frames (RGB + ~512px) before
                perceptual hashing (default: from config or True, FFF's behaviour).
                e621 archive's hasher never did this; see fff.hashing.archive_hasher.
            settings: fff addition. Use this dict instead of reading any config file.
        """
        # Load configuration from YAML if enabled
        config = {}
        if settings is not None:
            config = dict(settings)
        elif use_config:
            config = self._load_config_from_yaml(config_path)

        # Apply settings with priority: explicit parameters > config file > defaults
        self.hash_length = hash_length if hash_length is not None else config.get('hash_length', 16)
        self.audio_sample_size = audio_sample_size if audio_sample_size is not None else config.get('audio_sample_size', 65536)
        self.large_file_threshold = large_file_threshold if large_file_threshold is not None else config.get('large_file_threshold', 1048576)
        self.content_sample_size = content_sample_size if content_sample_size is not None else config.get('content_sample_size', 32768)
        self.image_hash_mode = image_hash_mode if image_hash_mode is not None else config.get('image_hash_mode', 'hybrid')
        self.image_sensitivity = image_sensitivity if image_sensitivity is not None else config.get('image_sensitivity', 'medium')
        self.compute_all_hashes = compute_all_hashes if compute_all_hashes is not None else config.get('compute_all_hashes', True)
        self.normalize_images = normalize_images if normalize_images is not None else config.get('normalize_images', True)

        # Set up logging
        self.logger = logging.getLogger(__name__)

        # Log configuration source for debugging
        if config and use_config:
            self.logger.debug(f"Loaded configuration from config.yaml: hash_length={self.hash_length}, "
                            f"image_hash_mode={self.image_hash_mode}, compute_all_hashes={self.compute_all_hashes}, "
                            f"large_file_threshold={self.large_file_threshold}")
        else:
            self.logger.debug("Using default configuration (config.yaml not found or disabled)")

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
            elif file_type == 'video':
                # Try perceptual hashing for videos if support is available
                return self._hash_video(path_obj)
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
        Hash image files using enhanced perceptual hashing

        Uses different algorithms based on the configured mode:
        - average: Basic average hash (fast but less precise)
        - dhash: Difference hash (good for detecting minor changes)
        - phash: Perceptual hash (robust against transformations)
        - whash: Wavelet hash (excellent for textured images)
        - hybrid: Combines dhash and phash for better accuracy
        - multi: Uses all algorithms for maximum differentiation

        NOTE: For images, we preserve the full perceptual hash representation
        to enable similarity matching via Hamming distance.
        """
        if not HAS_IMAGE_SUPPORT:
            self.logger.warning("PIL/imagehash not available, falling back to content hash")
            return self._hash_content(file_path)

        try:
            # Configure PIL to handle truncated/incomplete images
            from PIL import ImageFile
            ImageFile.LOAD_TRUNCATED_IMAGES = True

            with Image.open(file_path) as img:
                # Force load the image data to catch truncation issues early
                img.load()

                # ENHANCEMENT: Extract representative frame for animated GIFs
                # This ensures we can hash all frames, even for animated content
                img = self._extract_representative_frame(img)

                # ENHANCEMENT: Normalize image before hashing for better size-invariant matching
                # This helps match images with drastically different sizes and compression levels
                if self.normalize_images:
                    img = self._normalize_image_for_hashing(img)

                # Get hash size based on sensitivity
                hash_size = self._get_hash_size()

                # NEW: Option to always compute all hash types for maximum flexibility
                # This populates all database columns regardless of image_hash_mode setting
                if self.compute_all_hashes:
                    # Compute all 4 hash types
                    avg_hash = imagehash.average_hash(img, hash_size=hash_size)
                    d_hash = imagehash.dhash(img, hash_size=hash_size)
                    p_hash = imagehash.phash(img, hash_size=hash_size)
                    w_hash = imagehash.whash(img, hash_size=hash_size)

                    # Return as multi-format composite - database_manager will split into separate columns
                    # Format: avg|dhash|phash|whash (all 4 hash types)
                    composite = f"{avg_hash}|{d_hash}|{p_hash}|{w_hash}"
                    return composite

                # Original mode-based logic (backward compatibility)
                if self.image_hash_mode == 'average':
                    img_hash = imagehash.average_hash(img, hash_size=hash_size)
                    return str(img_hash)

                elif self.image_hash_mode == 'dhash':
                    img_hash = imagehash.dhash(img, hash_size=hash_size)
                    return str(img_hash)

                elif self.image_hash_mode == 'phash':
                    img_hash = imagehash.phash(img, hash_size=hash_size)
                    return str(img_hash)

                elif self.image_hash_mode == 'whash':
                    img_hash = imagehash.whash(img, hash_size=hash_size)
                    return str(img_hash)

                elif self.image_hash_mode == 'hybrid':
                    # Combine dhash and phash for better differentiation
                    dhash_val = imagehash.dhash(img, hash_size=hash_size)
                    phash_val = imagehash.phash(img, hash_size=hash_size)

                    # Return as composite - database_manager will split into separate columns
                    # Format: dhash|phash
                    composite = f"{dhash_val}|{phash_val}"
                    return composite

                elif self.image_hash_mode == 'multi':
                    # Use all available algorithms for maximum precision
                    # Calculate all hash types
                    avg_hash = imagehash.average_hash(img, hash_size=hash_size)
                    d_hash = imagehash.dhash(img, hash_size=hash_size)
                    p_hash = imagehash.phash(img, hash_size=hash_size)
                    w_hash = imagehash.whash(img, hash_size=hash_size)

                    # Return as composite - database_manager will split into separate columns
                    # Format: avg|dhash|phash|whash
                    composite = f"{avg_hash}|{d_hash}|{p_hash}|{w_hash}"
                    return composite

                else:
                    # Default to average hash
                    img_hash = imagehash.average_hash(img, hash_size=hash_size)
                    return str(img_hash)

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

    def _hash_video(self, file_path: Path) -> Optional[str]:
        """
        Hash video files using perceptual hashing of an extracted frame.
        
        Attempts to extract a representative frame from the video and hash it
        using the same perceptual hashing algorithms as images. Falls back to
        content hashing if frame extraction fails or video support is unavailable.
        
        Args:
            file_path: Path to video file
            
        Returns:
            Hash string (perceptual or content-based)
        """
        # Try to extract a frame and hash it perceptually
        if HAS_VIDEO_SUPPORT and HAS_IMAGE_SUPPORT:
            frame = self._extract_video_frame(file_path)
            
            if frame is not None:
                try:
                    # Normalize the extracted frame
                    if self.normalize_images:
                        frame = self._normalize_image_for_hashing(frame)
                    
                    # Get hash size based on sensitivity
                    hash_size = self._get_hash_size()
                    
                    # Use same hashing logic as images
                    if self.compute_all_hashes:
                        # Compute all 4 hash types
                        avg_hash = imagehash.average_hash(frame, hash_size=hash_size)
                        d_hash = imagehash.dhash(frame, hash_size=hash_size)
                        p_hash = imagehash.phash(frame, hash_size=hash_size)
                        w_hash = imagehash.whash(frame, hash_size=hash_size)
                        
                        # Return as multi-format composite
                        composite = f"{avg_hash}|{d_hash}|{p_hash}|{w_hash}"
                        return composite
                    
                    # Mode-based hashing (backward compatibility)
                    elif self.image_hash_mode == 'average':
                        return str(imagehash.average_hash(frame, hash_size=hash_size))
                    elif self.image_hash_mode == 'dhash':
                        return str(imagehash.dhash(frame, hash_size=hash_size))
                    elif self.image_hash_mode == 'phash':
                        return str(imagehash.phash(frame, hash_size=hash_size))
                    elif self.image_hash_mode == 'whash':
                        return str(imagehash.whash(frame, hash_size=hash_size))
                    elif self.image_hash_mode == 'hybrid':
                        dhash_val = imagehash.dhash(frame, hash_size=hash_size)
                        phash_val = imagehash.phash(frame, hash_size=hash_size)
                        return f"{dhash_val}|{phash_val}"
                    elif self.image_hash_mode == 'multi':
                        avg_hash = imagehash.average_hash(frame, hash_size=hash_size)
                        d_hash = imagehash.dhash(frame, hash_size=hash_size)
                        p_hash = imagehash.phash(frame, hash_size=hash_size)
                        w_hash = imagehash.whash(frame, hash_size=hash_size)
                        return f"{avg_hash}|{d_hash}|{p_hash}|{w_hash}"
                    else:
                        # Default to phash
                        return str(imagehash.phash(frame, hash_size=hash_size))
                        
                except Exception as e:
                    self.logger.warning(f"Error hashing extracted video frame from {file_path}: {e}")
        
        # Fallback to content hashing if frame extraction not available or failed
        self.logger.debug(f"Using content hash for video: {file_path}")
        return self._hash_content(file_path)

    def _get_hash_size(self) -> int:
        """Get hash size based on sensitivity setting - must be power of 2 for imagehash library"""
        if self.image_sensitivity == 'low':
            return 8    # 8x8 = 64-bit hash (groups similar images, more lenient)
        elif self.image_sensitivity == 'high':
            return 32   # 32x32 = 1024-bit hash (excellent detail preservation)
        else:  # medium
            return 16   # 16x16 = 256-bit hash (balanced sensitivity, RECOMMENDED)

    def _extract_representative_frame(self, img: 'Image.Image') -> 'Image.Image':
        """
        Extract a representative frame from animated images (GIFs) or use first frame.
        
        For animated GIFs, seeks to a middle frame to avoid intro/outro frames
        that might be blank or not representative of the content.
        
        Args:
            img: PIL Image object (may be animated)
            
        Returns:
            PIL Image object of a single frame
        """
        try:
            # Check if image is animated (has multiple frames)
            if hasattr(img, 'n_frames') and img.n_frames > 1:
                # Seek to middle frame for better representation
                middle_frame = img.n_frames // 2
                img.seek(middle_frame)
                # Convert the frame to avoid issues with palette/animation modes
                frame = img.convert('RGBA')
                return frame
            else:
                # Static image, return as-is
                return img
        except (EOFError, AttributeError):
            # If seeking fails or no frames info, use current frame
            return img

    def _extract_video_frame(self, file_path: Path) -> Optional['Image.Image']:
        """
        Extract a representative frame from a video file for perceptual hashing.
        
        Extracts a frame from 25% into the video to avoid intro frames,
        black screens, or credits that might not be representative.
        
        Args:
            file_path: Path to video file
            
        Returns:
            PIL Image object of the extracted frame, or None if extraction fails
        """
        if not HAS_VIDEO_SUPPORT:
            self.logger.debug("OpenCV not available, cannot extract video frames")
            return None
        
        # Initialize variables for log level restoration
        import os
        old_log_level = None
        old_cv_log = None
            
        try:
            # Suppress FFmpeg warnings (e.g., VP9 codec warnings for WebM files)
            # These are harmless but clutter output
            old_log_level = os.environ.get('OPENCV_LOG_LEVEL')
            os.environ['OPENCV_LOG_LEVEL'] = 'ERROR'
            
            # Also set OpenCV's internal log level if available
            if hasattr(cv2, 'setLogLevel'):
                old_cv_log = cv2.getLogLevel()
                cv2.setLogLevel(0)  # 0 = Silent
            
            # Open video file
            cap = cv2.VideoCapture(str(file_path))
            
            if not cap.isOpened():
                # Restore log levels before returning
                if old_log_level is not None:
                    os.environ['OPENCV_LOG_LEVEL'] = old_log_level
                elif 'OPENCV_LOG_LEVEL' in os.environ:
                    del os.environ['OPENCV_LOG_LEVEL']
                if old_cv_log is not None and hasattr(cv2, 'setLogLevel'):
                    cv2.setLogLevel(old_cv_log)
                    
                self.logger.warning(f"Could not open video file: {file_path}")
                return None
            
            # Get total frame count
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            
            if total_frames <= 0:
                self.logger.warning(f"Could not determine frame count for: {file_path}")
                cap.release()
                
                # Restore log levels before returning
                if old_log_level is not None:
                    os.environ['OPENCV_LOG_LEVEL'] = old_log_level
                elif 'OPENCV_LOG_LEVEL' in os.environ:
                    del os.environ['OPENCV_LOG_LEVEL']
                if old_cv_log is not None and hasattr(cv2, 'setLogLevel'):
                    cv2.setLogLevel(old_cv_log)
                    
                return None
            
            # Seek to 25% into the video for representative frame
            target_frame = total_frames // 4
            cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
            
            # Read the frame
            ret, frame = cap.read()
            cap.release()
            
            # Restore log levels
            if old_log_level is not None:
                os.environ['OPENCV_LOG_LEVEL'] = old_log_level
            elif 'OPENCV_LOG_LEVEL' in os.environ:
                del os.environ['OPENCV_LOG_LEVEL']
            
            if old_cv_log is not None and hasattr(cv2, 'setLogLevel'):
                cv2.setLogLevel(old_cv_log)
            
            if not ret or frame is None:
                self.logger.warning(f"Could not read frame from video: {file_path}")
                return None
            
            # Convert BGR (OpenCV) to RGB (PIL)
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            
            # Convert numpy array to PIL Image
            pil_image = Image.fromarray(frame_rgb)
            
            return pil_image
            
        except Exception as e:
            # Restore log levels on error
            try:
                if old_log_level is not None:
                    os.environ['OPENCV_LOG_LEVEL'] = old_log_level
                elif 'OPENCV_LOG_LEVEL' in os.environ:
                    del os.environ['OPENCV_LOG_LEVEL']
                
                if old_cv_log is not None and hasattr(cv2, 'setLogLevel'):
                    cv2.setLogLevel(old_cv_log)
            except Exception:
                pass
            
            self.logger.warning(f"Error extracting frame from video {file_path}: {e}")
            return None

    def _normalize_image_for_hashing(self, img: 'Image.Image') -> 'Image.Image':
        """
        Normalize image before perceptual hashing for better size-invariant matching.

        This preprocessing helps match images that are:
        - Drastically different sizes (thumbnails vs full resolution)
        - Different compression levels (high vs low quality JPEG)
        - Different formats (PNG vs JPEG)

        Args:
            img: PIL Image object

        Returns:
            Normalized PIL Image object
        """
        # Convert to RGB (normalize color mode)
        # This ensures consistent hashing regardless of original format (RGBA, LA, P, etc.)
        if img.mode not in ('RGB', 'L'):  # Keep grayscale as is
            if img.mode == 'RGBA':
                # Handle transparency by compositing on white background
                background = Image.new('RGB', img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[3])  # Use alpha channel as mask
                img = background
            else:
                img = img.convert('RGB')

        # Resize to a standard intermediate size before hashing
        # This helps normalize compression artifacts and size differences
        # The imagehash library will resize again, but this pre-normalization helps
        standard_size = 512  # Use 512px as intermediate size (good balance)

        # Only resize if image is significantly different from standard size
        if img.width > standard_size * 1.5 or img.height > standard_size * 1.5 or \
           img.width < standard_size * 0.5 or img.height < standard_size * 0.5:
            # Calculate scaling to fit within standard_size while maintaining aspect ratio
            img.thumbnail((standard_size, standard_size), Image.Resampling.LANCZOS)

        return img

    def _format_hash(self, hash_str: str) -> str:
        """Format hash string to desired length"""
        if len(hash_str) >= self.hash_length:
            return hash_str[:self.hash_length]
        else:
            # If hash is shorter than desired length, pad with repeated SHA256 hashing
            current = hash_str
            while len(current) < self.hash_length:
                # Add SHA256 of current string to extend it
                current += hashlib.sha256(current.encode()).hexdigest()
            return current[:self.hash_length]

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
                return self._format_hash(hash_obj.hexdigest())
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
                return self._format_hash(hash_obj.hexdigest())

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
            return f'perceptual_{self.image_hash_mode}_{self.image_sensitivity}'
        elif file_type == 'video' and HAS_VIDEO_SUPPORT and HAS_IMAGE_SUPPORT:
            return f'video_perceptual_{self.image_hash_mode}_{self.image_sensitivity}'
        elif file_type == 'video':
            return 'video_content'
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
def hash_file(file_path: str | Path,
              hash_length: Optional[int] = None,
              image_hash_mode: Optional[str] = None,
              image_sensitivity: Optional[str] = None,
              use_config: bool = True) -> Optional[str]:
    """
    Simple function to hash a single file with enhanced image hashing

    Args:
        file_path: Path to file
        hash_length: Length of returned hash (default: from config or 16)
        image_hash_mode: Hashing algorithm for images (default: from config or 'hybrid')
        image_sensitivity: Sensitivity level (default: from config or 'medium')
        use_config: Whether to load settings from config.yaml (default: True)

    Returns:
        Hash string or None
    """
    hasher = MediaHasher(
        hash_length=hash_length,
        image_hash_mode=image_hash_mode,
        image_sensitivity=image_sensitivity,
        use_config=use_config
    )
    return hasher.hash_file(file_path)


def get_file_info(file_path: str | Path,
                  image_hash_mode: Optional[str] = None,
                  image_sensitivity: Optional[str] = None,
                  use_config: bool = True) -> Dict[str, Any]:
    """
    Get comprehensive file information including enhanced hash

    Args:
        file_path: Path to file
        image_hash_mode: Hashing algorithm for images (default: from config or 'hybrid')
        image_sensitivity: Sensitivity level (default: from config or 'medium')
        use_config: Whether to load settings from config.yaml (default: True)

    Returns:
        Dictionary with file information
    """
    hasher = MediaHasher(
        image_hash_mode=image_hash_mode,
        image_sensitivity=image_sensitivity,
        use_config=use_config
    )
    return hasher.get_file_info(file_path)
# Support for different hash algorithms
class PerceptualHasher(MediaHasher):
    """Specialized hasher that uses enhanced perceptual hashing for all images"""

    def __init__(self, image_hash_mode: str = 'multi', image_sensitivity: str = 'high', **kwargs):
        """Initialize with high-precision image hashing settings"""
        super().__init__(image_hash_mode=image_hash_mode, image_sensitivity=image_sensitivity, **kwargs)

    def _hash_image(self, file_path: Path) -> Optional[str]:
        if not HAS_IMAGE_SUPPORT:
            raise ImportError("PIL and imagehash required for perceptual hashing")
        return super()._hash_image(file_path)


class HighSensitivityHasher(MediaHasher):
    """Hasher optimized for detecting even tiny differences between images"""

    def __init__(self, **kwargs):
        super().__init__(
            image_hash_mode='multi',
            image_sensitivity='high',
            hash_length=32,  # Longer hashes for more precision
            **kwargs
        )


class FastHasher(MediaHasher):
    """Hasher optimized for speed with reasonable accuracy"""

    def __init__(self, **kwargs):
        super().__init__(
            image_hash_mode='dhash',  # dhash is faster than multi but better than average
            image_sensitivity='medium',
            **kwargs
        )


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


# Perceptual hash comparison utilities
def compare_perceptual_hashes(hash1: str, hash2: str) -> Optional[int]:
    """
    Calculate Hamming distance between two perceptual hashes.

    Lower distance means more similar images. A distance of 0 means identical hashes.
    Typical thresholds:
    - 0-5: Very similar or identical images
    - 6-10: Similar images with minor differences
    - 11-20: Related but noticeably different
    - 21+: Different images

    Args:
        hash1: First perceptual hash string
        hash2: Second perceptual hash string

    Returns:
        Hamming distance (number of differing bits), or None if hashes are incompatible
    """
    if not HAS_IMAGE_SUPPORT:
        return None

    try:
        # Handle composite hashes (hybrid/multi mode)
        if '|' in hash1 and '|' in hash2:
            parts1 = hash1.split('|')
            parts2 = hash2.split('|')

            if len(parts1) != len(parts2):
                return None

            # Calculate average distance across all hash components
            total_distance = 0
            for p1, p2 in zip(parts1, parts2):
                h1 = imagehash.hex_to_hash(p1)
                h2 = imagehash.hex_to_hash(p2)
                total_distance += h1 - h2

            # Return average distance
            return total_distance // len(parts1)

        # Handle simple hashes
        h1 = imagehash.hex_to_hash(hash1)
        h2 = imagehash.hex_to_hash(hash2)

        return h1 - h2

    except Exception:
        return None


def are_images_similar(hash1: str, hash2: str, threshold: int = 10) -> bool:
    """
    Determine if two images are similar based on their perceptual hashes.

    Args:
        hash1: First perceptual hash
        hash2: Second perceptual hash
        threshold: Maximum Hamming distance to consider similar (default: 10)

    Returns:
        True if images are similar within threshold
    """
    distance = compare_perceptual_hashes(hash1, hash2)
    if distance is None:
        return False
    return distance <= threshold


def get_similarity_score(hash1: str, hash2: str, max_distance: int = 64) -> float:
    """
    Get a similarity score between 0.0 and 1.0 for two perceptual hashes.

    Args:
        hash1: First perceptual hash
        hash2: Second perceptual hash
        max_distance: Maximum expected distance for normalization (default: 64)

    Returns:
        Similarity score where 1.0 is identical and 0.0 is completely different
    """
    distance = compare_perceptual_hashes(hash1, hash2)
    if distance is None:
        return 0.0

    # Convert distance to similarity score (inverse relationship)
    similarity = max(0.0, 1.0 - (distance / max_distance))
    return similarity
