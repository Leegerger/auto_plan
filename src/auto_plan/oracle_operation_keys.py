"""One reservation, replay and permanent-void protocol for separate Oracle keys."""
import hashlib
import oracledb
from .access import StoreUnavailable
from .plans import PlanProblem, PlanBusy, PlanUnknown
from .oracle_plans import document, encode, decode, code


class OracleOperationKeys:
    def __init__(self, plans, *, demand=False):
        self.plans = plans
        self.demand = demand
        self.table = 'AP_DEMAND_KEY' if demand else 'AP_PLAN_KEY'

    def _read(self, cursor, key, actor, kind=None, sha=None):
        extra = '' if self.demand else ',OPERATION_KIND'
        cursor.execute(f'SELECT ACTOR_ID,STATUS,REQUEST_SHA,RESULT_BLOB{extra} FROM {self.table} WHERE OPERATION_ID=:key', key=key)
        row = cursor.fetchone()
        if row is None:
            return None
        if row[0] != actor or (kind is not None and row[1] == 'COMMITTED' and
                              (row[2] != sha or (not self.demand and row[4] != kind))):
            raise PlanProblem(409, '需求操作编号已用于其他请求' if self.demand else '操作编号已用于其他请求，请使用原请求核实')
        result = decode(row[3])
        if kind is not None and row[1] == 'ABORTED_VOID':
            raise PlanProblem(409, '前次需求操作已作废，请重新发起' if self.demand else '前次操作已作废，请由用户重新发起', result=result)
        return result

    def _insert(self, cursor, key, actor, now, status, result, kind, sha=None):
        columns = 'OPERATION_ID,ACTOR_ID,STATUS,REQUEST_SHA,RESULT_BLOB,RECORDED_AT'
        values = ':key,:actor,:status,:sha,:result,:now'
        parameters = dict(key=key, actor=actor, status=status, sha=sha, result=encode(result), now=now)
        if not self.demand:
            columns += ',OPERATION_KIND'
            if kind == 'verify':
                values += ",'verify'"
            else:
                values += ',:kind'
                parameters['kind'] = kind
        cursor.setinputsizes(result=oracledb.DB_TYPE_BLOB)
        cursor.execute(f'INSERT INTO {self.table} ({columns}) VALUES ({values})', **parameters)

    def _winner(self, connection, cursor, key, actor, kind=None, sha=None):
        connection.rollback()
        self.plans.authorize(cursor, actor, True)
        result = self._read(cursor, key, actor, kind, sha)
        if result is None:
            raise StoreUnavailable
        return result

    def submit(self, command, actor, now, kind, action):
        key = str(command.operation_id)
        sha = hashlib.sha256(document(command.model_dump(mode='json'))).hexdigest()
        committing = False
        try:
            with self.plans.pool.acquire() as connection:
                connection.call_timeout = 15000
                try:
                    with connection.cursor() as cursor:
                        self.plans.authorize(cursor, actor, True)
                        replayed = self._read(cursor, key, actor, kind, sha)
                        if replayed is not None:
                            return replayed
                        head = self.plans.head(cursor, True)
                        try:
                            self._insert(cursor, key, actor, now, 'COMMITTED', {}, kind, sha)
                        except oracledb.IntegrityError as error:
                            if code(error) != 1:
                                raise
                            return self._winner(connection, cursor, key, actor, kind, sha)
                        unit = self.plans.unit(cursor, head, None if self.demand else key, lock=True)
                        self.plans.authorize(cursor, actor, True)
                        result = action(unit)
                        cursor.setinputsizes(result=oracledb.DB_TYPE_BLOB)
                        cursor.execute(f'UPDATE {self.table} SET RESULT_BLOB=:result WHERE OPERATION_ID=:key', result=encode(result), key=key)
                    committing = True
                    connection.commit()
                    return result
                except BaseException:
                    connection.rollback()
                    raise
        except oracledb.Error as error:
            if committing:
                raise PlanUnknown from None
            if code(error) in (30006, 54):
                raise PlanBusy from None
            raise StoreUnavailable from None

    def verify(self, key, actor, now):
        try:
            with self.plans.pool.acquire() as connection:
                connection.call_timeout = 15000
                try:
                    with connection.cursor() as cursor:
                        self.plans.authorize(cursor, actor, True)
                        result = self._read(cursor, key, actor)
                        if result is not None:
                            return result
                        result = {'status': 'ABORTED_VOID', 'operation_id': key}
                        try:
                            self._insert(cursor, key, actor, now, 'ABORTED_VOID', result, 'verify')
                        except oracledb.IntegrityError as error:
                            if code(error) != 1:
                                raise
                            return self._winner(connection, cursor, key, actor)
                    connection.commit()
                    return result
                except BaseException:
                    connection.rollback()
                    raise
        except oracledb.Error:
            # A reservation may win while verification waits. Only an
            # acknowledged commit or a durable winner establishes the outcome.
            raise PlanUnknown from None
