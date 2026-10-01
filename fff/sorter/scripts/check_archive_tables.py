# fff: implemented in media sorter/check_archive_tables.py.
# fff: module-level code moved into main(argv=None) so importing it does nothing.
"""Check archive tables in database."""
from fff.sorter.handlers.database_manager import DatabaseManager
from fff.sorter.config import Config



def main(argv=None):  # fff: the original ran at import time; same statements, now in main()
    config = Config('configs/config.yaml')
    db = DatabaseManager(config)

    conn = db.get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SHOW TABLES LIKE 'archive_%'")
    tables = cursor.fetchall()

    print('\nArchive tables in database:')
    for table in tables:
        print(f'  - {table[0]}')

    cursor.close()
    conn.close()

    print(f'\nTotal: {len(tables)} archive tables found')


if __name__ == "__main__":
    main()

