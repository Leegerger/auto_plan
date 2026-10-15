"""Immutable demand files, complete-input epochs and independent idempotency keys."""
import hashlib
from uuid import UUID
import oracledb
from .oracle_plans import document,encode,decode,code
from .input_models import WeeklyDemand
from .access import StoreUnavailable
from .plans import PlanProblem,PlanBusy,PlanUnknown


class OracleDemandStore:
    def __init__(self,plans): self.plans=plans;self.pool=plans.pool
    def files_in_transaction(self,cursor,version):
        cursor.execute('SELECT DOCUMENT_BLOB,SHA256 FROM AP_DEMAND_FILE WHERE INPUT_VERSION<=:version ORDER BY ATTRIBUTION_DATE,FILE_VERSION',version=version)
        return [decode(blob,sha) for blob,sha in cursor]
    def read(self,actor,action): return self.plans.read(actor,lambda unit:action(unit,self.files_in_transaction(unit.cursor,unit.inputs.input_version)))
    def insert_file(self,cursor,record,version):
        from datetime import date,datetime
        record=record|{'input_version':version}
        raw=document(record)
        cursor.setinputsizes(blob=oracledb.DB_TYPE_BLOB)
        cursor.execute('INSERT INTO AP_DEMAND_FILE (FILE_ID,ATTRIBUTION_DATE,FILE_VERSION,INPUT_VERSION,DOCUMENT_BLOB,SHA256,ACTOR_ID,RECORDED_AT) VALUES (:identity,:day,:file_version,:version,:blob,:sha,:actor,:now)',identity=record['file_id'],day=date.fromisoformat(record['attribution_date']),file_version=record['version'],version=version,blob=encode(record),sha=hashlib.sha256(raw).hexdigest(),actor=record['actor'],now=datetime.fromisoformat(record['recorded_at']))
    def append(self,unit,record,demands,actor,now):
        def transform(current): return current.model_copy(update={'input_version':current.input_version+1,'business':current.business.model_copy(update={'demands':[WeeklyDemand.model_validate(r) for r in demands]})})
        inputs=self.plans.input_store.update_in_transaction(unit.cursor,actor,now,transform)
        self.insert_file(unit.cursor,record,inputs.input_version)
        return {'status':'COMMITTED','input_version':inputs.input_version,'file_id':record['file_id'],'attribution_date':record['attribution_date'],'version':record['version']}
    def _key(self,cursor,key,actor,sha=None):
        cursor.execute('SELECT ACTOR_ID,REQUEST_SHA,STATUS,RESULT_BLOB FROM AP_DEMAND_KEY WHERE OPERATION_ID=:key',key=key)
        row=cursor.fetchone()
        if not row:return None
        if row[0]!=actor or (sha is not None and row[2]=='COMMITTED' and row[1]!=sha): raise PlanProblem(409,'需求操作编号已用于其他请求')
        result=decode(row[3])
        if sha is not None and row[2]=='ABORTED_VOID': raise PlanProblem(409,'前次需求操作已作废，请重新发起',result=result)
        return result
    def submit(self,payload,actor,now,action):
        key=str(payload.operation_id);sha=hashlib.sha256(document(payload.model_dump(mode='json'))).hexdigest();committing=False
        try:
            with self.pool.acquire() as connection:
                connection.call_timeout=15000
                try:
                    with connection.cursor() as cursor:
                        self.plans.authorize(cursor,actor,True)
                        old=self._key(cursor,key,actor,sha)
                        if old is not None:return old
                        head=self.plans.head(cursor,True)
                        cursor.setinputsizes(result=oracledb.DB_TYPE_BLOB)
                        try:cursor.execute("INSERT INTO AP_DEMAND_KEY (OPERATION_ID,ACTOR_ID,REQUEST_SHA,STATUS,RESULT_BLOB,RECORDED_AT) VALUES (:key,:actor,:sha,'COMMITTED',:result,:now)",key=key,actor=actor,sha=sha,result=encode({}),now=now)
                        except oracledb.IntegrityError as exc:
                            if code(exc)!=1:raise
                            connection.rollback();self.plans.authorize(cursor,actor,True)
                            result=self._key(cursor,key,actor,sha)
                            if result is None:raise StoreUnavailable
                            return result
                        unit=self.plans.unit(cursor,head,lock=True)
                        self.plans.authorize(cursor,actor,True)
                        result=action(unit,self.files_in_transaction(cursor,unit.inputs.input_version))
                        cursor.setinputsizes(result=oracledb.DB_TYPE_BLOB)
                        cursor.execute('UPDATE AP_DEMAND_KEY SET RESULT_BLOB=:result WHERE OPERATION_ID=:key',result=encode(result),key=key)
                    committing=True;connection.commit();return result
                except BaseException:connection.rollback();raise
        except oracledb.Error as exc:
            if committing:raise PlanUnknown from None
            if code(exc) in (30006,54):raise PlanBusy from None
            raise StoreUnavailable from None
    def verify(self,key,actor,now):
        try:
            with self.pool.acquire() as connection:
                connection.call_timeout=15000
                try:
                    with connection.cursor() as cursor:
                        self.plans.authorize(cursor,actor,True)
                        result=self._key(cursor,key,actor)
                        if result is not None:return result
                        result={'status':'ABORTED_VOID','operation_id':key}
                        cursor.setinputsizes(result=oracledb.DB_TYPE_BLOB)
                        try:cursor.execute("INSERT INTO AP_DEMAND_KEY (OPERATION_ID,ACTOR_ID,STATUS,RESULT_BLOB,RECORDED_AT) VALUES (:key,:actor,'ABORTED_VOID',:result,:now)",key=key,actor=actor,result=encode(result),now=now)
                        except oracledb.IntegrityError as exc:
                            if code(exc)!=1:raise
                            connection.rollback();self.plans.authorize(cursor,actor,True)
                            result=self._key(cursor,key,actor)
                            if result is None:raise StoreUnavailable
                            return result
                    connection.commit();return result
                except BaseException:connection.rollback();raise
        except oracledb.Error:raise PlanUnknown from None
    def check_consistency(self):
        def check(unit,files):
            identities={r['file_id'] for r in files}
            effective={}
            for record in files:
                UUID(record['file_id'])
                if any(row['ordinal']!=i for i,row in enumerate(record['rows'])):raise StoreUnavailable
                if len({row['row_id'] for row in record['rows']})!=len(record['rows']):raise StoreUnavailable
                effective[record['attribution_date']]=record
            totals={}
            for record in effective.values():
                for row in record['rows']:
                    key=(row['model_code'],row['week']);totals[key]=totals.get(key,0)+row['delta']
                    if totals[key]<0:raise StoreUnavailable
            actual={(r.model_code,r.week.isoformat()):r.quantity for r in unit.inputs.business.demands}
            if totals!=actual:raise StoreUnavailable
            initial=unit.context.lineage.get('demand_initial_file')
            if initial and initial not in identities:raise StoreUnavailable
            unit.cursor.execute('SELECT STATUS,RESULT_BLOB FROM AP_DEMAND_KEY')
            for status,blob in unit.cursor:
                result=decode(blob)
                if status!=result.get('status') or (status=='COMMITTED' and result.get('file_id') not in identities):raise StoreUnavailable
            return True
        # Startup has no logged-in principal, validate with an ordinary read-only cursor.
        with self.pool.acquire() as connection:
            try:
                with connection.cursor() as cursor:
                    head=self.plans.head(cursor);unit=self.plans.unit(cursor,head)
                    return check(unit,self.files_in_transaction(cursor,unit.inputs.input_version))
            finally:connection.rollback()
