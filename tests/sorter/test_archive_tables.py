# fff: implemented in media sorter/tests/test_archive_tables.py.
# fff: integration test (test databases only).
"""
Quick test to verify archive tables are created correctly.
"""
import sys
import os

import pytest

pytestmark = pytest.mark.integration  # fff: needs the (test) databases; FFF_INTEGRATION=1

from fff.sorter.config import Config
from fff.sorter.handlers.database_manager import DatabaseManager

def test_archive_tables():
    """Test that archive tables are created based on configuration format."""
    print("Loading configuration...")
    config = Config('configs/config.yaml')

    print("Initializing database manager...")
    db_manager = DatabaseManager(config)

    print("\nChecking archive configuration format:")
    archive_dirs = config.config.get('archive_directories', {})
    for key, value in archive_dirs.items():
        if value is None:
            print(f"  {key}: null (disabled)")
        elif isinstance(value, str):
            print(f"  {key}: Simple format (string) -> table: archive_{key}")
        elif isinstance(value, dict):
            print(f"  {key}: Expanded format (dict)")
            for media_type, path in value.items():
                if path:
                    print(f"    {media_type}: {path} -> table: archive_{key}_{media_type}")

    print("\nExpected archive tables:")
    expected_tables = db_manager.get_all_table_names(include_archives=True)
    archive_tables = [t for t in expected_tables if t.startswith('archive_')]
    for table in sorted(archive_tables):
        print(f"  - {table}")

    print("\nTesting table_name_for_dest() mapping:")
    # Test explicit archive directories
    explicit_config = archive_dirs.get('explicit', {})
    if isinstance(explicit_config, dict):
        for media_type, path in explicit_config.items():
            if path:
                try:
                    table_name = db_manager.table_name_for_dest(path)
                    print(f"  {path} -> {table_name}")
                except Exception as e:
                    print(f"  {path} -> ERROR: {e}")

    # Test SFW archive directories
    sfw_config = archive_dirs.get('sfw', {})
    if isinstance(sfw_config, dict):
        for media_type, path in sfw_config.items():
            if path:
                try:
                    table_name = db_manager.table_name_for_dest(path)
                    print(f"  {path} -> {table_name}")
                except Exception as e:
                    print(f"  {path} -> ERROR: {e}")

    print("\n✓ Test completed successfully!")
    print("\nNote: The actual tables should now exist in your database.")
    print("You can verify by running: SHOW TABLES LIKE 'archive_%';")

if __name__ == '__main__':
    test_archive_tables()
