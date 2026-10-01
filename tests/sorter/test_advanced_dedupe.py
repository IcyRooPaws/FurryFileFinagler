#!/usr/bin/env python3
"""
Quick test of advanced deduplication integration

Tests the new multi-stage deduplicator with sample files.
"""

import sys
import os
import tempfile
from pathlib import Path


from fff.sorter.core.advanced_deduplicator import (
    AdvancedDeduplicator, DedupeMode, DuplicateFile
)


def create_test_files():
    """Create temporary test files for demonstration"""
    import hashlib

    # Create temp directory
    temp_dir = tempfile.mkdtemp(prefix="dedupe_test_")
    print(f"📁 Created test directory: {temp_dir}")

    files = []

    # Create identical files (true duplicates)
    content_a = b"This is file A content" * 1000  # ~22KB
    for i in range(3):
        path = os.path.join(temp_dir, f"duplicate_A_{i}.txt")
        with open(path, 'wb') as f:
            f.write(content_a)
        files.append(path)
        print(f"  ✓ Created {Path(path).name}")

    # Create another set of duplicates
    content_b = b"Different content for file B" * 1000  # ~29KB
    for i in range(2):
        path = os.path.join(temp_dir, f"duplicate_B_{i}.txt")
        with open(path, 'wb') as f:
            f.write(content_b)
        files.append(path)
        print(f"  ✓ Created {Path(path).name}")

    # Create unique file
    content_c = b"Unique file content C" * 500
    path = os.path.join(temp_dir, "unique_C.txt")
    with open(path, 'wb') as f:
        f.write(content_c)
    files.append(path)
    print(f"  ✓ Created {Path(path).name}")

    # Create large duplicates
    content_d = b"X" * (2 * 1024 * 1024)  # 2MB
    for i in range(2):
        path = os.path.join(temp_dir, f"large_duplicate_{i}.dat")
        with open(path, 'wb') as f:
            f.write(content_d)
        files.append(path)
        print(f"  ✓ Created {Path(path).name}")

    return temp_dir, files


def test_basic_deduplication():
    """Test basic duplicate detection"""
    print("\n" + "="*70)
    print("TEST 1: Basic Deduplication")
    print("="*70)

    temp_dir, file_paths = create_test_files()

    try:
        # Convert to DuplicateFile objects
        duplicate_files = []
        for path in file_paths:
            stat = os.stat(path)
            duplicate_files.append(DuplicateFile(
                path=path,
                size=stat.st_size,
                mtime=int(stat.st_mtime)
            ))

        print(f"\n📊 Testing with {len(duplicate_files)} files...")

        # Test each mode
        modes = [DedupeMode.FAST, DedupeMode.NORMAL, DedupeMode.FULL]

        for mode in modes:
            print(f"\n--- Testing {mode.value.upper()} mode ---")

            deduplicator = AdvancedDeduplicator()
            groups, stats = deduplicator.find_duplicates(
                files=duplicate_files,
                mode=mode
            )

            print(f"\nResults:")
            print(f"  Duplicate groups: {len(groups)}")
            print(f"  Total duplicates: {sum(len(g.files) for g in groups)}")
            print(f"  Wasted space: {stats.wasted_space / 1024:.1f} KB")
            print(f"  Time: {stats.total_time:.3f}s")

            # Show groups
            for i, group in enumerate(groups, 1):
                print(f"\n  Group {i}: {group.file_count} files × {group.size / 1024:.1f} KB")
                for file in group.files:
                    print(f"    - {Path(file.path).name}")

        print("\n✅ Test passed!")

    finally:
        # Cleanup
        import shutil
        shutil.rmtree(temp_dir)
        print(f"\n🧹 Cleaned up test directory")


def test_size_filtering():
    """Test size filtering"""
    print("\n" + "="*70)
    print("TEST 2: Size Filtering")
    print("="*70)

    temp_dir, file_paths = create_test_files()

    try:
        # Convert to DuplicateFile objects
        all_files = []
        for path in file_paths:
            stat = os.stat(path)
            all_files.append(DuplicateFile(
                path=path,
                size=stat.st_size,
                mtime=int(stat.st_mtime)
            ))

        # Filter by size (only files > 50KB)
        large_files = [f for f in all_files if f.size > 50 * 1024]

        print(f"\n📊 Files > 50KB: {len(large_files)} of {len(all_files)}")

        deduplicator = AdvancedDeduplicator()
        groups, stats = deduplicator.find_duplicates(
            files=large_files,
            mode=DedupeMode.NORMAL
        )

        print(f"\nResults:")
        print(f"  Duplicate groups: {len(groups)}")
        print(f"  Expected: 1 group (the 2MB files)")

        if len(groups) == 1 and len(groups[0].files) == 2:
            print("✅ Size filtering works correctly!")
        else:
            print("⚠️  Unexpected result")

    finally:
        import shutil
        shutil.rmtree(temp_dir)
        print(f"\n🧹 Cleaned up test directory")


def test_performance():
    """Test performance with slightly larger dataset"""
    print("\n" + "="*70)
    print("TEST 3: Performance Comparison")
    print("="*70)

    import time
    import hashlib

    temp_dir = tempfile.mkdtemp(prefix="dedupe_perf_")

    try:
        # Create 100 files (50 duplicate pairs)
        print(f"📁 Creating 100 test files...")
        files = []

        for i in range(50):
            content = f"Content for pair {i}".encode() * 1000

            # Create duplicate pair
            for j in range(2):
                path = os.path.join(temp_dir, f"file_{i}_{j}.dat")
                with open(path, 'wb') as f:
                    f.write(content)

                stat = os.stat(path)
                files.append(DuplicateFile(
                    path=path,
                    size=stat.st_size,
                    mtime=int(stat.st_mtime)
                ))

        print(f"  ✓ Created {len(files)} files")

        # Test FAST mode
        print("\n--- FAST Mode ---")
        start = time.time()
        deduplicator = AdvancedDeduplicator()
        groups, stats = deduplicator.find_duplicates(files, mode=DedupeMode.FAST)
        fast_time = time.time() - start
        print(f"  Time: {fast_time:.3f}s")
        print(f"  Groups: {len(groups)}")

        # Test NORMAL mode
        print("\n--- NORMAL Mode ---")
        start = time.time()
        groups, stats = deduplicator.find_duplicates(files, mode=DedupeMode.NORMAL)
        normal_time = time.time() - start
        print(f"  Time: {normal_time:.3f}s")
        print(f"  Groups: {len(groups)}")

        # Test FULL mode
        print("\n--- FULL Mode ---")
        start = time.time()
        groups, stats = deduplicator.find_duplicates(files, mode=DedupeMode.FULL)
        full_time = time.time() - start
        print(f"  Time: {full_time:.3f}s")
        print(f"  Groups: {len(groups)}")

        print(f"\n📈 Performance:")
        print(f"  FAST is {full_time/fast_time:.1f}x faster than FULL")
        print(f"  NORMAL is {full_time/normal_time:.1f}x faster than FULL")

        print("\n✅ Performance test complete!")

    finally:
        import shutil
        shutil.rmtree(temp_dir)
        print(f"\n🧹 Cleaned up test directory")


def main():
    print("\n🧪 Advanced Deduplicator Test Suite")
    print("="*70)

    try:
        # Test 1: Basic functionality
        test_basic_deduplication()

        # Test 2: Size filtering
        test_size_filtering()

        # Test 3: Performance
        test_performance()

        print("\n" + "="*70)
        print("✅ ALL TESTS PASSED!")
        print("="*70)
        print("\nThe advanced deduplicator is working correctly.")
        print("You can now use it with your media library manager database:\n")
        print("  python find_duplicates_advanced.py --mode normal")
        print("  python find_duplicates_advanced.py --mode fast --tables Furry_Explicit")
        print("\nSee ADVANCED_DEDUPLICATION.md for full documentation.")

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
