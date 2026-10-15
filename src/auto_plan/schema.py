"""Read-only compatibility check against operator-recorded migration checksums."""
import hashlib
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[2] / 'migrations'
SCHEMA_MIN = SCHEMA_MAX = 5


def manifest(directory=MIGRATIONS):
    return [(int(path.name[:3]),path.name,hashlib.sha256(path.read_bytes()).hexdigest())
            for path in sorted(directory.glob('[0-9][0-9][0-9]_*.sql')) if not path.name.startswith('000_')]


def verify_schema(pool):
    with pool.acquire() as connection:
        connection.call_timeout=15000
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT VERSION_NO,FILENAME,SHA256,STATUS FROM AP_SCHEMA_MIGRATION ORDER BY VERSION_NO')
                rows=list(cursor)
                expected=manifest()
                if not rows or not SCHEMA_MIN<=max(row[0] for row in rows)<=SCHEMA_MAX:
                    raise ValueError('schema_range')
                if len(rows)!=len(expected) or any(tuple(row[:3])!=item or row[3] not in {'APPLIED','BASELINED'} for row,item in zip(rows,expected)):
                    raise ValueError('schema_checksum_or_state')
        finally:
            connection.rollback()
    return True
