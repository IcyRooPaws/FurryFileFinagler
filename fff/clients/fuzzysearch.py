"""FuzzySearch reverse image search (implemented in reverse-image search/fluffle.py)."""
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

def search_image_fuzzysearch(image_path: Path, api_key: str, distance: int = 3) -> Optional[List[Dict[str, Any]]]:
    """Search for images using FuzzySearch API."""
    try:
        api_base = "https://api-next.fuzzysearch.net"
        headers = {"x-api-key": api_key}
        data = {"distance": distance} if distance is not None else {}

        print(f"  Searching FuzzySearch: {image_path.name}")

        with open(image_path, "rb") as f:
            files = {"image": (image_path.name, f)}
            response = requests.post(
                f"{api_base}/v1/image",
                headers=headers,
                files=files,
                data=data if data else None,
                timeout=30,
            )

        if response.status_code == 400:
            print(f"    FuzzySearch error: Invalid image")
            return None
        if response.status_code == 401:
            print(f"    FuzzySearch error: Invalid or missing API key")
            return None
        if response.status_code == 413:
            print(f"    FuzzySearch error: Image file is too large")
            return None
        if response.status_code == 429:
            print(f"    FuzzySearch error: Rate limit exhausted")
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
