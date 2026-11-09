"""Client for FuzzySearch reverse-image search."""
from pathlib import Path
from typing import Any, Dict, List, Optional

from fff.clients import http

def search_image_fuzzysearch(image_path: Path, api_key: str, distance: int = 3) -> Optional[List[Dict[str, Any]]]:
    """Search for images using FuzzySearch API."""
    try:
        api_base = "https://api-next.fuzzysearch.net"
        headers = {"x-api-key": api_key}
        data = {"distance": distance} if distance is not None else {}

        print(f"  Searching FuzzySearch: {image_path.name}")

        with open(image_path, "rb") as f:
            files = {"image": (image_path.name, f)}
            response = http.post(
                "fuzzysearch",
                f"{api_base}/v1/image",
                headers=headers,
                files=files,
                data=data if data else None,
                timeout=30,
            )

        if response.status_code == 400:
            print("    FuzzySearch error: Invalid image")
            return None
        if response.status_code == 401:
            print("    FuzzySearch error: Invalid or missing API key")
            return None
        if response.status_code == 413:
            print("    FuzzySearch error: Image file is too large")
            return None
        if response.status_code == 429:
            print("    FuzzySearch error: Rate limit exhausted")
            return None

        if response.status_code != 200:
            print(f"    FuzzySearch error: Status code {response.status_code}")
            return None

        results = response.json()
        if results:
            print(f"    FuzzySearch found {len(results)} match(es)")
        return results

    except Exception as e:
        print(f"    FuzzySearch error: {e}")
        return None
