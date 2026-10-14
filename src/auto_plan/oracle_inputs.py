"""Versioned input persistence over the application's existing Oracle pool."""
import hashlib
import json
import zlib
from contextlib import contextmanager
import oracledb
from .access import AuthorizationDenied, StoreUnavailable
from .input_models import InputSnapshot, normalize_snapshot


class OracleInputStore:
    def __init__(self, pool):
        self.pool = pool

    @contextmanager
    def transaction(self):
        try:
            with self.pool.acquire() as connection:
                connection.call_timeout = 15000
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("ALTER SESSION SET TIME_ZONE='+00:00'")
                        yield cursor
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
        except oracledb.Error:
            raise StoreUnavailable from None

    @staticmethod
    def _head(cursor, lock=False):
        cursor.execute('SELECT CURRENT_VERSION FROM AP_INPUT_HEAD WHERE ID=1' + (' FOR UPDATE WAIT 10' if lock else ''))
        row = cursor.fetchone()
        if not row:
            raise StoreUnavailable
        return int(row[0])

    @staticmethod
    def _read(cursor, version):
        if version == 0:
            return InputSnapshot()
        cursor.execute('SELECT SNAPSHOT_BLOB, SHA256 FROM AP_INPUT_REVISION WHERE INPUT_VERSION=:version', version=version)
        row = cursor.fetchone()
        if not row:
            raise KeyError(version)
        try:
            compressed = row[0].read() if hasattr(row[0], 'read') else row[0]
            document = zlib.decompress(compressed)
            if hashlib.sha256(document).hexdigest() != row[1]:
                raise StoreUnavailable
            snapshot = normalize_snapshot(json.loads(document))
            if snapshot.input_version != version:
                raise StoreUnavailable
            return snapshot
        except (ValueError, zlib.error):
            raise StoreUnavailable from None

    def read(self, version=None):
        with self.transaction() as cursor:
            return self._read(cursor, self._head(cursor) if version is None else version)

    def update(self, actor, now, transform):
        with self.transaction() as cursor:
            version = self._head(cursor, lock=True)
            if actor != 'SYSTEM_MONTH_CONTINUATION':
                cursor.execute('SELECT EDITOR, ACTIVE FROM AP_AUTHORIZATION WHERE USER_ID=:actor', actor=actor)
                authorization = cursor.fetchone()
                if not authorization or not authorization[0] or not authorization[1]:
                    raise AuthorizationDenied
            current = self._read(cursor, version)
            candidate = transform(current)
            if candidate == current:
                return current
            if candidate.input_version != version + 1:
                raise ValueError('Input transform must create the next version')
            document = json.dumps(candidate.model_dump(mode='json'), ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
            cursor.setinputsizes(snapshot=oracledb.DB_TYPE_BLOB)
            cursor.execute('INSERT INTO AP_INPUT_REVISION (INPUT_VERSION, SNAPSHOT_BLOB, SHA256, ACTOR_ID, RECORDED_AT) VALUES (:version, :snapshot, :sha256, :actor, :now)', version=candidate.input_version, snapshot=zlib.compress(document), sha256=hashlib.sha256(document).hexdigest(), actor=actor, now=now)
            cursor.execute('UPDATE AP_INPUT_HEAD SET CURRENT_VERSION=:version WHERE ID=1', version=candidate.input_version)
            return candidate
