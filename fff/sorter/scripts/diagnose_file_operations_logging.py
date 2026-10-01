# fff: renamed from test_file_operations_logging.py (a diagnostic, not a pytest test).
# fff: still writes test rows to file_operations and deletes them afterwards, in the active
# fff: environment's sorter schema (Config() is the environment-aware sorter view); logs go to logs/sorter.
"""
Test File Operations Logging

This script tests the file operations logging system by performing
a few sample operations and verifying they're logged to the database.
"""
import os
import sys
import tempfile
import shutil

from fff.sorter.handlers.utils import Config, setup_logging, move_file_with_logging, copy_file_with_logging, delete_file_with_logging
from fff.sorter.handlers.database_manager import DatabaseManager
from fff.sorter import paths as sorter_paths

def test_file_operations_logging():
    """Test the file operations logging system."""
    print("=" * 70)
    print("File Operations Logging Test")
    print("=" * 70)
    
    # Load configuration
    try:
        config = Config()
        print("✓ Configuration loaded")
    except Exception as e:
        print(f"✗ Failed to load configuration: {e}")
        return False
    
    # Setup logging
    logger = setup_logging(log_directory=str(sorter_paths.LOG_DIR), config=config)  # fff: logs/sorter
    print("✓ Logger initialized")
    
    # Initialize database manager
    try:
        db_manager = DatabaseManager(config)
        print("✓ Database manager initialized")
    except Exception as e:
        print(f"✗ Failed to initialize database manager: {e}")
        return False
    
    # Check if file_operations table exists
    try:
        conn = db_manager.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES LIKE 'file_operations'")
        table_exists = cursor.fetchone() is not None
        cursor.close()
        conn.close()
        
        if table_exists:
            print("✓ file_operations table exists")
        else:
            print("✗ file_operations table does not exist")
            return False
    except Exception as e:
        print(f"✗ Failed to check table: {e}")
        return False
    
    # Create temporary test files
    print("\n" + "-" * 70)
    print("Creating test files...")
    print("-" * 70)
    
    temp_dir = tempfile.mkdtemp(prefix="fff_sorter_test_")
    test_file1 = os.path.join(temp_dir, "test_file_1.txt")
    test_file2 = os.path.join(temp_dir, "test_file_2.txt")
    test_file_moved = os.path.join(temp_dir, "test_file_moved.txt")
    test_file_copied = os.path.join(temp_dir, "test_file_copied.txt")
    
    # Create test files
    with open(test_file1, 'w') as f:
        f.write("This is a test file for move operation")
    with open(test_file2, 'w') as f:
        f.write("This is a test file for copy operation")
    
    print(f"✓ Created test files in {temp_dir}")
    
    # Test move operation
    print("\n" + "-" * 70)
    print("Testing MOVE operation...")
    print("-" * 70)
    
    try:
        move_file_with_logging(
            source_path=test_file1,
            destination_path=test_file_moved,
            db_manager=db_manager,
            logger_obj=logger,
            source_directory="test_source",
            destination_directory="test_destination",
            reason="test_move_operation"
        )
        print(f"✓ Moved {os.path.basename(test_file1)} -> {os.path.basename(test_file_moved)}")
    except Exception as e:
        print(f"✗ Move operation failed: {e}")
        shutil.rmtree(temp_dir)
        return False
    
    # Test copy operation
    print("\n" + "-" * 70)
    print("Testing COPY operation...")
    print("-" * 70)
    
    try:
        copy_file_with_logging(
            source_path=test_file2,
            destination_path=test_file_copied,
            db_manager=db_manager,
            logger_obj=logger,
            source_directory="test_source",
            destination_directory="test_destination",
            reason="test_copy_operation"
        )
        print(f"✓ Copied {os.path.basename(test_file2)} -> {os.path.basename(test_file_copied)}")
    except Exception as e:
        print(f"✗ Copy operation failed: {e}")
        shutil.rmtree(temp_dir)
        return False
    
    # Test delete operation
    print("\n" + "-" * 70)
    print("Testing DELETE operation...")
    print("-" * 70)
    
    try:
        delete_file_with_logging(
            file_path=test_file_copied,
            db_manager=db_manager,
            logger_obj=logger,
            source_directory="test_destination",
            reason="test_delete_operation"
        )
        print(f"✓ Deleted {os.path.basename(test_file_copied)}")
    except Exception as e:
        print(f"✗ Delete operation failed: {e}")
        shutil.rmtree(temp_dir)
        return False
    
    # Verify database entries
    print("\n" + "-" * 70)
    print("Verifying database entries...")
    print("-" * 70)
    
    try:
        conn = db_manager.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Count operations in last minute
        cursor.execute("""
            SELECT operation_type, COUNT(*) as count
            FROM file_operations
            WHERE performed_at >= DATE_SUB(NOW(), INTERVAL 1 MINUTE)
              AND reason LIKE 'test_%'
            GROUP BY operation_type
            ORDER BY operation_type
        """)
        
        results = cursor.fetchall()
        
        if len(results) == 0:
            print("✗ No operations found in database")
            cursor.close()
            conn.close()
            shutil.rmtree(temp_dir)
            return False
        
        print("\nLogged operations:")
        for row in results:
            print(f"  - {row['operation_type']}: {row['count']} operation(s)")
        
        # Get detailed operation logs
        cursor.execute("""
            SELECT operation_type, file_name, source_directory, 
                   destination_directory, reason, operation_status
            FROM file_operations
            WHERE performed_at >= DATE_SUB(NOW(), INTERVAL 1 MINUTE)
              AND reason LIKE 'test_%'
            ORDER BY performed_at
        """)
        
        operations = cursor.fetchall()
        print(f"\n✓ Found {len(operations)} test operation(s) in database")
        
        print("\nDetailed logs:")
        for op in operations:
            print(f"  [{op['operation_type']}] {op['file_name']}")
            print(f"    Source: {op['source_directory']}")
            print(f"    Destination: {op['destination_directory']}")
            print(f"    Reason: {op['reason']}")
            print(f"    Status: {op['operation_status']}")
            print()
        
        cursor.close()
        conn.close()
        
    except Exception as e:
        print(f"✗ Failed to query database: {e}")
        shutil.rmtree(temp_dir)
        return False
    
    # Cleanup
    print("-" * 70)
    print("Cleaning up test files...")
    print("-" * 70)
    
    try:
        # Delete remaining test files
        if os.path.exists(test_file2):
            os.remove(test_file2)
        if os.path.exists(test_file_moved):
            os.remove(test_file_moved)
        
        # Remove temp directory
        shutil.rmtree(temp_dir)
        print(f"✓ Cleaned up test directory: {temp_dir}")
    except Exception as e:
        print(f"⚠ Warning: Failed to cleanup: {e}")
    
    # Cleanup test database entries
    try:
        conn = db_manager.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM file_operations
            WHERE reason LIKE 'test_%'
              AND performed_at >= DATE_SUB(NOW(), INTERVAL 1 MINUTE)
        """)
        deleted = cursor.rowcount
        conn.commit()
        cursor.close()
        conn.close()
        print(f"✓ Cleaned up {deleted} test database entries")
    except Exception as e:
        print(f"⚠ Warning: Failed to cleanup database: {e}")
    
    print("\n" + "=" * 70)
    print("✓ All tests passed! File operations logging is working correctly.")
    print("=" * 70)
    
    return True

def main(argv=None):  # fff: the original __main__ block (no options); returns the exit code
    try:
        success = test_file_operations_logging()
        return 0 if success else 1
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        return 1
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
