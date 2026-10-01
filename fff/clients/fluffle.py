"""Client for reverse-image source search."""
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests
from PIL import Image

def resize_image_for_upload(image_path: Path, max_size_mb: float = 4.0, temp_dir: Path = None,
                           max_megapixels: float = 16.0) -> Tuple[Optional[Path], bool]:
    """
    Resize an image if it exceeds the max size limit or pixel area limit.

    Args:
        image_path: Path to the original image
        max_size_mb: Maximum file size in megabytes (default 4.0 MB)
        temp_dir: Temporary directory to store resized image
        max_megapixels: Maximum pixel area in megapixels (default 16.0 MP)

    Returns:
        Tuple of (path_to_use, is_temp) where path_to_use is the image to upload
        and is_temp indicates if it's a temporary file that should be cleaned up
    """
    max_size_bytes = max_size_mb * 1024 * 1024
    max_pixels = int(max_megapixels * 1_000_000)
    file_size = image_path.stat().st_size

    # Check pixel area
    needs_resize = False
    try:
        with Image.open(image_path) as img:
            pixel_area = img.width * img.height
            if pixel_area > max_pixels:
                print(f"    [i] Image area ({pixel_area:,} pixels) exceeds limit ({max_pixels:,} pixels), resizing...")
                needs_resize = True
    except Exception as e:
        print(f"    Warning: Could not check image dimensions: {e}")

    # If file is within both limits, use original
    if file_size <= max_size_bytes and not needs_resize:
        return image_path, False

    try:
        # Create temp directory if not provided
        if temp_dir is None:
            temp_dir = Path(tempfile.gettempdir()) / "fluffle_temp"
        temp_dir.mkdir(exist_ok=True)

        print(f"    [i] Image size ({file_size / 1024 / 1024:.2f} MB) exceeds limit, resizing...")

        # Open and resize image
        with Image.open(image_path) as img:
            # Convert RGBA to RGB if saving as JPEG
            if img.mode in ('RGBA', 'LA', 'P'):
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                background.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
                img = background

            # Calculate resize ratio to get under limits
            # Start with 85% of original size and adjust
            ratio = 0.85

            # If initial pixel area exceeds limit, calculate ratio to get under pixel limit
            initial_area = img.width * img.height
            if initial_area > max_pixels:
                # Calculate ratio needed to get under pixel limit
                pixel_ratio = (max_pixels / initial_area) ** 0.5
                ratio = min(ratio, pixel_ratio * 0.95)  # Use 95% of calculated ratio for safety

            temp_path = temp_dir / f"temp_{image_path.name}"

            # Keep resizing until we're under the limit
            max_attempts = 20  # Safety limit to prevent infinite loop
            for attempt in range(max_attempts):
                new_width = int(img.width * ratio)
                new_height = int(img.height * ratio)

                # Ensure dimensions are at least 1 pixel
                if new_width < 1 or new_height < 1:
                    print(f"    [WARNING] Image too small to resize further")
                    break

                # Check if pixel area is within limit
                new_area = new_width * new_height
                if new_area > max_pixels:
                    # Reduce ratio to get under pixel limit
                    ratio *= (max_pixels / new_area) ** 0.5 * 0.95
                    continue

                resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

                # Save with quality optimization
                save_kwargs = {'optimize': True}
                if image_path.suffix.lower() in ['.jpg', '.jpeg']:
                    save_kwargs['quality'] = 85
                    save_format = 'JPEG'
                elif image_path.suffix.lower() == '.png':
                    save_format = 'PNG'
                elif image_path.suffix.lower() == '.webp':
                    save_kwargs['quality'] = 85
                    save_format = 'WEBP'
                else:
                    save_format = 'JPEG'

                resized.save(temp_path, format=save_format, **save_kwargs)

                # Check if we're under both limits
                temp_size = temp_path.stat().st_size
                if temp_size <= max_size_bytes and new_area <= max_pixels:
                    megapixels = new_area / 1_000_000
                    print(f"    [i] Resized to {new_width}x{new_height} ({megapixels:.1f}MP, {temp_size / 1024 / 1024:.2f} MB)")
                    return temp_path, True

                # Reduce ratio more aggressively if still too large
                if temp_size > max_size_bytes * 1.5:
                    # Much larger, reduce by 15%
                    ratio -= 0.15
                elif temp_size > max_size_bytes * 1.2:
                    # Somewhat larger, reduce by 10%
                    ratio -= 0.10
                else:
                    # Close to target, reduce by 5%
                    ratio -= 0.05

            # If we still couldn't get it small enough, use the last attempt
            print(f"    [WARNING] Could not reduce below limit after {max_attempts} attempts ({temp_size / 1024 / 1024:.2f} MB)")
            return temp_path, True

    except Exception as e:
        print(f"    Error resizing image: {e}")
        return image_path, False


def search_image_fluffle(image_path: Path, limit: int = 10) -> Optional[Dict[str, Any]]:
    """Search for posts matching the given image file using reverse search API."""
    temp_file = None
    is_temp = False

    try:
        url = "https://api.fluffle.xyz/exact-search-by-file"

        # Check file size and resize if necessary
        upload_path, is_temp = resize_image_for_upload(image_path)
        if is_temp:
            temp_file = upload_path

        # Determine MIME type based on file extension
        ext = upload_path.suffix.lower()
        mime_types = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.webp': 'image/webp'
        }
        mime_type = mime_types.get(ext, 'image/jpeg')

        with open(upload_path, "rb") as img_file:
            files = {
                "File": (image_path.name, img_file, mime_type)  # Use original name
            }
            payload = {"Limit": str(limit)}
            headers = {"accept": "application/json"}

            print(f"  Searching: {image_path.name}")
            response = requests.post(url, data=payload, files=files, headers=headers, timeout=30)

        # Clean up temp file if created
        if is_temp and temp_file and temp_file.exists():
            try:
                temp_file.unlink()
            except Exception as e:
                print(f"    Warning: Could not delete temp file: {e}")

        if response.status_code == 429:
            print(f"    Error: Rate limited (429). Waiting 5 seconds...")
            time.sleep(5)
            return None

        if response.status_code == 415:
            print(f"    Error: Unsupported file type")
            return {"error": "unsupportedFileType", "matches": []}

        if response.status_code == 422:
            print(f"    Error: Corrupt file")
            return {"error": "corruptFile", "matches": []}

        if response.status_code == 400:
            error_text = response.text[:200]
            # Check if it's a file size error that we should have caught
            if "larger than the maximum allowed size" in response.text:
                print(f"    Error: File still too large after resize attempt")
            else:
                print(f"    Error: Bad request - {error_text}")
            return {"error": "badRequest", "matches": []}

        if response.status_code != 200:
            print(f"    Error: Received status code {response.status_code}")
            return None

        try:
            results = response.json()
            return results
        except ValueError:
            print(f"    Error: Invalid JSON response")
            return None

    except Exception as e:
        print(f"    Error searching image: {e}")
        return None
    finally:
        # Ensure temp file is cleaned up even if there's an exception
        if is_temp and temp_file and temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass
