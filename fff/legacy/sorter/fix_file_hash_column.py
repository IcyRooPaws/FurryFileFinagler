# fff: implemented in media sorter/fix_file_hash_column.py.
# fff: one-off migration kept for reference; main(argv=None) (no options).
"""
Quick migration script to fix file_hash column size in file_operations table.

The column was VARCHAR(128) but composite perceptual hashes can be up to 259+ characters.
This script updates it to VARCHAR(512) to accommodate all hash types.
"""
import mysql.connector
from fff.sorter.handlers.utils import Config

def fix_file_hash_column():
    """Alter the file_hash column to VARCHAR(512)."""
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
        
        print("Modifying file_hash column from VARCHAR(128) to VARCHAR(512)...")
        cursor.execute("""
            ALTER TABLE file_operations 
            MODIFY COLUMN file_hash VARCHAR(512)
        """)
        
        conn.commit()
        print("✓ Successfully updated file_hash column!")
        
        # Verify the change
        cursor.execute("""
            SHOW COLUMNS FROM file_operations 
            WHERE Field = 'file_hash'
        """)
        result = cursor.fetchone()
        print(f"✓ Current column definition: {result}")
        
    except Exception as e:
        print(f"✗ Error: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()
        print("Done!")

def main(argv=None):  # fff: entry point (no options)
    fix_file_hash_column()


if __name__ == "__main__":
    main()
