# fff: implemented in media sorter/tests/test_artist_config.py.
# fff: integration test (reads the real config).
"""
Test that artist processing config is loaded correctly.
"""
import sys
import os

import pytest

pytestmark = pytest.mark.integration  # fff: needs the (test) databases; FFF_INTEGRATION=1

from fff.sorter.handlers import Config

def test_artist_config():
    """Test that artist processing config loads correctly."""
    print("=" * 60)
    print("ARTIST PROCESSING CONFIG TEST")
    print("=" * 60)
    
    print("\n[1] Loading configuration...")
    config = Config('configs/config.yaml')
    
    print("\n[2] Checking artist processing settings...")
    skip_archives = getattr(config, 'skip_archives_in_artist_scan', None)
    
    if skip_archives is None:
        print("    ✗ skip_archives_in_artist_scan not found in config")
        print("    This means the config wasn't loaded properly")
        return False
    else:
        print(f"    ✓ skip_archives_in_artist_scan: {skip_archives}")
        print(f"    Type: {type(skip_archives)}")
    
    print("\n[3] Testing default behavior...")
    if skip_archives:
        print("    ✓ Archives will be SKIPPED during artist scan (fast)")
        print("    This is the recommended setting for performance")
    else:
        print("    ⚠ Archives will be INCLUDED in artist scan (slow)")
        print("    This may take significantly longer")
    
    print("\n" + "=" * 60)
    print("CONFIG TEST COMPLETE")
    print("=" * 60)
    print("\nConfiguration summary:")
    print(f"  - Artist directory: {config.artist_directory}")
    print(f"  - Favorites directory: {config.fav_directory}")
    print(f"  - Skip archives in artist scan: {skip_archives}")
    print("=" * 60)
    
    return True

if __name__ == '__main__':
    success = test_artist_config()
    sys.exit(0 if success else 1)
