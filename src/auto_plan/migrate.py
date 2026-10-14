"""Explicit operator-run Oracle migrations. Startup never changes the schema."""
import argparse
from pathlib import Path
import oracledb
from .oracle_access import OracleAccessStore
from .settings import Settings


def statements(document: str):
    pending = []
    plsql = False
    for line in document.splitlines():
        if not line.strip() or line.lstrip().startswith('--'):
            continue
        if line.upper().startswith('CREATE OR REPLACE TRIGGER'):
            plsql = True
        if plsql and line.strip() == '/':
            yield '\n'.join(pending)
            pending = []
            plsql = False
        else:
            pending.append(line)
            if not plsql and line.rstrip().endswith(';'):
                yield '\n'.join(pending).rstrip().removesuffix(';')
                pending = []
    if pending:
        raise ValueError('Migration has an unterminated statement')


def apply(settings: Settings, migration: Path, expected_user: str):
    if settings.oracle_user.upper() != expected_user.upper():
        raise ValueError('Configured Oracle account differs from expected account')
    store = OracleAccessStore.connect(settings)
    try:
        with store.pool.acquire() as connection:
            connection.call_timeout = 15000
            with connection.cursor() as cursor:
                cursor.execute("SELECT SYS_CONTEXT('USERENV','SESSION_USER') FROM DUAL")
                if cursor.fetchone()[0].upper() != expected_user.upper():
                    raise ValueError('Connected schema differs from expected account')
                for statement in statements(migration.read_text()):
                    cursor.execute(statement)
            connection.commit()
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('migration', type=Path)
    parser.add_argument('--expected-user', required=True)
    arguments = parser.parse_args()
    try:
        apply(Settings(), arguments.migration, arguments.expected_user)
    except oracledb.Error as error:
        detail = error.args[0] if error.args else None
        raise SystemExit(f"Migration failed: {getattr(detail, 'full_code', 'Oracle error')}") from None
    print(f'Applied {arguments.migration.name}')


if __name__ == '__main__':
    main()
