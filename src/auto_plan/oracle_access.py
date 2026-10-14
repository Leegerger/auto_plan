import json
from contextlib import contextmanager
from datetime import timezone
import oracledb
from .access import Principal, AuthorizationDenied, LastAdministrator, StoreUnavailable, IDLE_LIFETIME, ABSOLUTE_LIFETIME


class OracleAccessStore:
    def __init__(self, pool):
        self.pool = pool

    @classmethod
    def connect(cls, settings):
        if not settings.oracle_dsn or not settings.oracle_password.get_secret_value():
            raise ValueError("Oracle DSN and password must be configured")
        if oracledb.is_thin_mode():
            oracledb.init_oracle_client(lib_dir=settings.oracle_client_path)
        pool = oracledb.create_pool(
            user=settings.oracle_user, password=settings.oracle_password.get_secret_value(),
            dsn=settings.oracle_dsn, min=1, max=settings.oracle_pool_max, increment=1,
            getmode=oracledb.POOL_GETMODE_TIMEDWAIT, wait_timeout=10000,
            tcp_connect_timeout=5, retry_count=0,
        )
        return cls(pool)

    @contextmanager
    def transaction(self):
        try:
            with self.pool.acquire() as connection:
                connection.call_timeout = 15000
                try:
                    with connection.cursor() as cursor:
                        # Python's datetime binding must not depend on an Oracle
                        # account's session time zone when storing UTC lifetimes.
                        cursor.execute("ALTER SESSION SET TIME_ZONE='+00:00'")
                        yield cursor
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
        except oracledb.Error:
            raise StoreUnavailable from None

    @staticmethod
    def principal(row):
        return Principal(row[0], frozenset(role for role, enabled in zip(("viewer", "editor", "administrator"), row[1:4]) if enabled))

    @staticmethod
    def _lock(cursor):
        cursor.execute("SELECT ID FROM AP_ACCESS_HEAD WHERE ID=1 FOR UPDATE WAIT 10")
        if cursor.fetchone() is None:
            raise StoreUnavailable

    @classmethod
    def _admin(cls, cursor, actor):
        cursor.execute("SELECT USER_ID, VIEWER, EDITOR, ADMINISTRATOR, ACTIVE FROM AP_AUTHORIZATION WHERE USER_ID=:actor", actor=actor)
        row = cursor.fetchone()
        if not row or not row[4] or not row[3]:
            raise AuthorizationDenied

    @staticmethod
    def _audit(cursor, actor, user_id, action, roles, active, now):
        cursor.execute("INSERT INTO AP_AUTH_AUDIT (ACTOR_ID, USER_ID, ACTION, ROLES_JSON, ACTIVE, RECORDED_AT) VALUES (:actor, :user_id, :action, :roles, :active, :now)", actor=actor, user_id=user_id, action=action, roles=json.dumps(sorted(roles)), active=int(active), now=now)

    def startup(self):
        with self.transaction() as cursor:
            self._lock(cursor)
            cursor.execute("DELETE FROM AP_SESSION")

    def close(self):
        self.pool.close()

    def login(self, user_id, token_hash, now, initial_admin):
        with self.transaction() as cursor:
            self._lock(cursor)
            cursor.execute("SELECT COUNT(*) FROM AP_AUTHORIZATION")
            if cursor.fetchone()[0] == 0 and initial_admin and user_id == initial_admin:
                cursor.execute("INSERT INTO AP_AUTHORIZATION (USER_ID, ADMINISTRATOR) VALUES (:user_id, 1)", user_id=user_id)
                self._audit(cursor, user_id, user_id, "bootstrap", {"administrator"}, True, now)
            cursor.execute("SELECT USER_ID, VIEWER, EDITOR, ADMINISTRATOR, ACTIVE FROM AP_AUTHORIZATION WHERE USER_ID=:user_id", user_id=user_id)
            row = cursor.fetchone()
            if not row or not row[4] or not self.principal(row).roles:
                raise AuthorizationDenied
            cursor.execute("INSERT INTO AP_SESSION (TOKEN_HASH, USER_ID, CREATED_AT, LAST_SEEN_AT) VALUES (:token_hash, :user_id, :now, :now)", token_hash=token_hash, user_id=user_id, now=now)
            return self.principal(row)

    def session(self, token_hash, now):
        with self.transaction() as cursor:
            cursor.execute("SELECT USER_ID, CREATED_AT, LAST_SEEN_AT FROM AP_SESSION WHERE TOKEN_HASH=:token_hash FOR UPDATE WAIT 10", token_hash=token_hash)
            session = cursor.fetchone()
            if not session:
                return None
            user_id, created, seen = session
            # Oracle TIMESTAMP WITH TIME ZONE returns datetime; normalize a fixed UTC clock.
            created = created.replace(tzinfo=timezone.utc) if created.tzinfo is None else created
            seen = seen.replace(tzinfo=timezone.utc) if seen.tzinfo is None else seen
            cursor.execute("SELECT USER_ID, VIEWER, EDITOR, ADMINISTRATOR, ACTIVE FROM AP_AUTHORIZATION WHERE USER_ID=:user_id", user_id=user_id)
            row = cursor.fetchone()
            if now - created >= ABSOLUTE_LIFETIME or now - seen >= IDLE_LIFETIME or not row or not row[4] or not self.principal(row).roles:
                cursor.execute("DELETE FROM AP_SESSION WHERE TOKEN_HASH=:token_hash", token_hash=token_hash)
                return None
            cursor.execute("UPDATE AP_SESSION SET LAST_SEEN_AT=:now WHERE TOKEN_HASH=:token_hash", now=now, token_hash=token_hash)
            return self.principal(row)

    def logout(self, token_hash):
        with self.transaction() as cursor:
            cursor.execute("DELETE FROM AP_SESSION WHERE TOKEN_HASH=:token_hash", token_hash=token_hash)

    def authorizations(self, actor_id):
        with self.transaction() as cursor:
            self._admin(cursor, actor_id)
            cursor.execute("SELECT USER_ID, VIEWER, EDITOR, ADMINISTRATOR, ACTIVE FROM AP_AUTHORIZATION ORDER BY USER_ID")
            return [self.principal(row).public() | {"active": bool(row[4])} for row in cursor.fetchall()]

    def authorize(self, actor_id, user_id, roles, active, now):
        with self.transaction() as cursor:
            self._lock(cursor)
            self._admin(cursor, actor_id)
            cursor.execute("SELECT COUNT(*) FROM AP_AUTHORIZATION WHERE ACTIVE=1 AND ADMINISTRATOR=1 AND USER_ID<>:user_id", user_id=user_id)
            if cursor.fetchone()[0] == 0 and not (active and "administrator" in roles):
                raise LastAdministrator
            values = dict(user_id=user_id, viewer=int("viewer" in roles), editor=int("editor" in roles), administrator=int("administrator" in roles), active=int(active))
            cursor.execute("MERGE INTO AP_AUTHORIZATION a USING (SELECT :user_id USER_ID FROM DUAL) s ON (a.USER_ID=s.USER_ID) WHEN MATCHED THEN UPDATE SET a.VIEWER=:viewer, a.EDITOR=:editor, a.ADMINISTRATOR=:administrator, a.ACTIVE=:active WHEN NOT MATCHED THEN INSERT (USER_ID, VIEWER, EDITOR, ADMINISTRATOR, ACTIVE) VALUES (:user_id, :viewer, :editor, :administrator, :active)", values)
            if not active or not roles:
                cursor.execute("DELETE FROM AP_SESSION WHERE USER_ID=:user_id", user_id=user_id)
            self._audit(cursor, actor_id, user_id, "authorize", roles, active, now)
            return {"user_id": user_id, "roles": sorted(roles), "active": active}

    def audit(self, actor_id):
        with self.transaction() as cursor:
            self._admin(cursor, actor_id)
            cursor.execute("SELECT ID, ACTOR_ID, USER_ID, ACTION, ROLES_JSON, ACTIVE, RECORDED_AT FROM AP_AUTH_AUDIT ORDER BY ID DESC FETCH FIRST 200 ROWS ONLY")
            return [{"id": row[0], "actor_id": row[1], "user_id": row[2], "action": row[3], "roles": json.loads(row[4]), "active": bool(row[5]), "recorded_at": row[6].isoformat()} for row in cursor.fetchall()]
