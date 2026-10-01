# fff: one-off migration kept for reference; main(argv=None) (no options).
"""
Migration script to remove file_hash column from file_operations table.

The file_hash column is redundant (already stored in destination tables)
and was causing issues with composite hash sizes. We no longer log hashes
in file_operations.
"""
import mysql.connector
from fff.sorter.handlers.utils import Config

def remove_file_hash_column():
    """Drop the file_hash column from file_operations table."""
    config = Config()

    print("Connecting to database...")
    conn = mysql.connector.connect(
        host=config.MYSQL_HOST,
        port=config.MYSQL_PORT,
        user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD,
        database=config.MYSQL_DATABASE
    )

    try:
        cursor = conn.cursor()

        # Check if column exists first
        cursor.execute("""
            SHOW COLUMNS FROM file_operations
            WHERE Field = 'file_hash'
        """)
        result = cursor.fetchone()

        if result:
            print("Removing file_hash column from file_operations table...")
            cursor.execute("""
                ALTER TABLE file_operations
                DROP COLUMN file_hash
            """)

            conn.commit()
            print("✓ Successfully removed file_hash column!")
        else:
            print("✓ file_hash column already removed or doesn't exist")

        # Verify the removal
        cursor.execute("SHOW COLUMNS FROM file_operations")
        columns = cursor.fetchall()
        print(f"\n✓ Current table structure:")
        for col in columns:
            print(f"  - {col[0]} ({col[1]})")

    except Exception as e:
        print(f"✗ Error: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()
        print("\nDone!")

def main(argv=None):  # fff: entry point (no options)
    remove_file_hash_column()


if __name__ == "__main__":
    main()
