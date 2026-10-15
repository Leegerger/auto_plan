"""Single-commit Oracle plan transactions over the existing application pool."""
import hashlib
import json
import zlib
from itertools import islice
from datetime import date
import oracledb
from .access import AuthorizationDenied, StoreUnavailable
from .input_models import InputSnapshot
from .oracle_inputs import OracleInputStore
from .plan_models import Plan
from .plans import PlanningContext, PlanProblem, PlanBusy, PlanUnknown, cells


def document(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()


def encode(value): return zlib.compress(document(value))


def decode(blob,sha=None):
    try:
        raw=zlib.decompress(blob.read() if hasattr(blob,'read') else blob)
        if sha and hashlib.sha256(raw).hexdigest()!=sha: raise StoreUnavailable
        return json.loads(raw)
    except (ValueError,zlib.error): raise StoreUnavailable from None


def insert_batches(cursor,statement,rows):
    pending=iter(rows)
    while batch:=list(islice(pending,1000)):
        cursor.executemany(statement,batch)


def code(error): return getattr(error.args[0],'code',None) if error.args else None


def zero_revision():
    return {'snapshot':{'plan':Plan().model_dump(mode='json')},'coverage':[],'lineage':{},'operations':[],'effects':[]}


class OraclePlanUnit:
    def __init__(self,store,cursor,head,inputs,context,key=None):
        self.store,self.cursor,self.input_store=store,cursor,store.input_store
        self.revision,self.adoption=head
        self.inputs,self.context,self.key=inputs,context,key
        self.plan=Plan.model_validate(self.get_revision(self.revision)['snapshot']['plan'])

    def get_inputs(self,version): return self.input_store._read(self.cursor,version)

    def get_context(self,version): return self.store.context_in_transaction(self.cursor,version)

    def _record(self,table,column,identity):
        self.cursor.execute(f'SELECT S.SNAPSHOT_BLOB,S.SHA256 FROM {table} R JOIN AP_PLAN_SNAPSHOT S ON S.SHA256=R.SNAPSHOT_SHA WHERE R.{column}=:identity',identity=identity)
        row=self.cursor.fetchone()
        if not row: raise KeyError(identity)
        return decode(row[0],row[1])

    def get_revision(self,identity):
        return zero_revision() if identity==0 else self._record('AP_PLAN_REVISION','REVISION_NO',identity)

    def get_adoption(self,identity): return self._record('AP_PLAN_ADOPTION','ADOPTION_NO',identity)

    def effects_since(self,revision):
        self.cursor.execute('SELECT EFFECT_JSON FROM AP_PLAN_EFFECT WHERE REVISION_NO>:revision',revision=revision)
        return {tuple(json.loads(row[0])) for row in self.cursor}

    def append(self,prepared,operations,actor,now,kind,pending):
        revision=self.revision+1; adoption=self.adoption+(kind=='adopt')
        record=prepared|{'revision':revision,'latest_adoption':adoption,'operations':operations,'actor':actor,'recorded_at':now.isoformat(),'kind':kind,'pending':pending}
        raw=document(record); sha=hashlib.sha256(raw).hexdigest()
        self.cursor.execute('SELECT SHA256 FROM AP_PLAN_SNAPSHOT WHERE SHA256=:sha',sha=sha)
        if not self.cursor.fetchone():
            self.cursor.setinputsizes(blob=oracledb.DB_TYPE_BLOB)
            self.cursor.execute('INSERT INTO AP_PLAN_SNAPSHOT (SHA256,SNAPSHOT_BLOB,RECORDED_AT) VALUES (:sha,:blob,:now)',sha=sha,blob=zlib.compress(raw),now=now)
        self.cursor.execute('INSERT INTO AP_PLAN_REVISION (REVISION_NO,SNAPSHOT_SHA,INPUT_VERSION,ACTOR_ID,OPERATION_KIND,PENDING,RECORDED_AT) VALUES (:revision,:sha,:version,:actor,:kind,:pending,:now)',revision=revision,sha=sha,version=self.inputs.input_version,actor=actor,kind=kind,pending=int(pending),now=now)
        effect_rows=({'revision':revision,'sha':hashlib.sha256(document(effect)).hexdigest(),
                      'effect':document(effect).decode()} for effect in record['effects'])
        insert_batches(self.cursor,'INSERT INTO AP_PLAN_EFFECT (REVISION_NO,EFFECT_SHA,EFFECT_JSON) VALUES (:revision,:sha,:effect)',effect_rows)
        if kind=='adopt':
            self.cursor.execute('INSERT INTO AP_PLAN_ADOPTION (ADOPTION_NO,REVISION_NO,SNAPSHOT_SHA,ACTOR_ID,RECORDED_AT) VALUES (:adoption,:revision,:sha,:actor,:now)',adoption=adoption,revision=revision,sha=sha,actor=actor,now=now)
            cell_rows=({'adoption':adoption,'sha':hashlib.sha256(document(identity)).hexdigest(),
                        'day':date.fromisoformat(identity[0]),'line':identity[1],'model':identity[2],'quantity':quantity}
                       for identity,quantity in cells(Plan.model_validate(prepared['plan'])).items())
            insert_batches(self.cursor,'INSERT INTO AP_ADOPTED_CELL (ADOPTION_NO,CELL_SHA,PRODUCTION_DATE,LINE_CODE,MODEL_CODE,QUANTITY) VALUES (:adoption,:sha,:day,:line,:model,:quantity)',cell_rows)
        self.cursor.execute('INSERT INTO AP_PLAN_AUDIT (REVISION_NO,ACTOR_ID,OPERATION_KIND,OPERATION_ID,RECORDED_AT) VALUES (:revision,:actor,:kind,:key,:now)',revision=revision,actor=actor,kind=kind,key=self.key,now=now)
        self.cursor.execute('UPDATE AP_PLAN_HEAD SET CURRENT_REVISION=:revision,CURRENT_ADOPTION=:adoption WHERE ID=1',revision=revision,adoption=adoption)
        return {'status':'COMMITTED','revision':revision,'latest_adoption':adoption,'input_version':self.inputs.input_version,'pending':pending}


class OraclePlanStore:
    def __init__(self,pool):
        self.pool=pool; self.input_store=OracleInputStore(pool)

    @staticmethod
    def authorize(cursor,actor,editor=False):
        cursor.execute('SELECT EDITOR,VIEWER,ACTIVE FROM AP_AUTHORIZATION WHERE USER_ID=:actor',actor=actor)
        row=cursor.fetchone()
        if not row or not row[2] or not (row[0] if editor else row[0] or row[1]): raise AuthorizationDenied

    @staticmethod
    def head(cursor,lock=False):
        cursor.execute('SELECT CURRENT_REVISION,CURRENT_ADOPTION FROM AP_PLAN_HEAD WHERE ID=1'+(' FOR UPDATE WAIT 10' if lock else ''))
        row=cursor.fetchone()
        if not row: raise StoreUnavailable
        return tuple(map(int,row))

    @staticmethod
    def context_in_transaction(cursor,version):
        cursor.execute('SELECT CONTEXT_BLOB,SHA256 FROM AP_PLAN_CONTEXT WHERE INPUT_VERSION=(SELECT MAX(INPUT_VERSION) FROM AP_PLAN_CONTEXT WHERE INPUT_VERSION<=:version)',version=version)
        row=cursor.fetchone()
        return PlanningContext.model_validate(decode(row[0],row[1])) if row else PlanningContext()

    def unit(self,cursor,head,key=None,lock=False,version=None):
        inputs=self.input_store.read_in_transaction(cursor,lock=lock) if version is None else self.input_store._read(cursor,version)
        return OraclePlanUnit(self,cursor,head,inputs,self.context_in_transaction(cursor,inputs.input_version),key)

    def read(self,actor,action):
        try:
            with self.pool.acquire() as connection:
                connection.call_timeout=15000
                try:
                    with connection.cursor() as cursor:
                        self.authorize(cursor,actor)
                        # Pin both mutable heads in one statement SCN. All later
                        # reads use immutable records at these exact versions.
                        cursor.execute('SELECT H.CURRENT_REVISION,H.CURRENT_ADOPTION,I.CURRENT_VERSION FROM AP_PLAN_HEAD H CROSS JOIN AP_INPUT_HEAD I WHERE H.ID=1 AND I.ID=1')
                        pointers=cursor.fetchone()
                        if not pointers: raise StoreUnavailable
                        result=action(self.unit(cursor,tuple(map(int,pointers[:2])),version=int(pointers[2])))
                    connection.rollback()
                    return result
                except BaseException:
                    connection.rollback(); raise
        except oracledb.Error: raise StoreUnavailable from None

    def _key(self,cursor,key,actor,kind=None,request_sha=None):
        cursor.execute('SELECT ACTOR_ID,OPERATION_KIND,STATUS,REQUEST_SHA,RESULT_BLOB FROM AP_PLAN_KEY WHERE OPERATION_ID=:key',key=key)
        row=cursor.fetchone()
        if not row: return None
        if row[0]!=actor or (row[2]=='COMMITTED' and kind is not None and (row[1]!=kind or row[3]!=request_sha)):
            raise PlanProblem(409,'操作编号已用于其他请求，请使用原请求核实')
        result=decode(row[4])
        if row[2]=='ABORTED_VOID' and kind is not None:
            raise PlanProblem(409,'前次操作已作废，请由用户重新发起',result=result)
        return result

    def submit(self,command,actor,now,kind,action):
        key=str(command.operation_id); sha=hashlib.sha256(document(command.model_dump(mode='json'))).hexdigest()
        committing=False
        try:
            with self.pool.acquire() as connection:
                connection.call_timeout=15000
                try:
                    with connection.cursor() as cursor:
                        self.authorize(cursor,actor,True)
                        replayed=self._key(cursor,key,actor,kind,sha)
                        if replayed is not None: return replayed
                        head=self.head(cursor,True)
                        cursor.setinputsizes(result=oracledb.DB_TYPE_BLOB)
                        try:
                            cursor.execute('INSERT INTO AP_PLAN_KEY (OPERATION_ID,ACTOR_ID,OPERATION_KIND,STATUS,REQUEST_SHA,RESULT_BLOB,RECORDED_AT) VALUES (:key,:actor,:kind,\'COMMITTED\',:sha,:result,:now)',key=key,actor=actor,kind=kind,sha=sha,result=encode({}),now=now)
                        except oracledb.IntegrityError as error:
                            if code(error)!=1: raise
                            connection.rollback()
                            self.authorize(cursor,actor,True)
                            replayed=self._key(cursor,key,actor,kind,sha)
                            if replayed is None: raise StoreUnavailable
                            return replayed
                        unit=self.unit(cursor,head,key,lock=True)
                        self.authorize(cursor,actor,True)
                        result=action(unit)
                        cursor.setinputsizes(result=oracledb.DB_TYPE_BLOB)
                        cursor.execute('UPDATE AP_PLAN_KEY SET RESULT_BLOB=:result WHERE OPERATION_ID=:key',result=encode(result),key=key)
                    committing=True
                    connection.commit()
                    return result
                except BaseException:
                    connection.rollback(); raise
        except oracledb.Error as error:
            if committing: raise PlanUnknown from None
            if code(error) in (30006,54): raise PlanBusy from None
            raise StoreUnavailable from None

    def verify(self,key,actor,now):
        committing=False
        try:
            with self.pool.acquire() as connection:
                connection.call_timeout=15000
                try:
                    with connection.cursor() as cursor:
                        self.authorize(cursor,actor,True)
                        result=self._key(cursor,key,actor)
                        if result is not None: return result
                        result={'status':'ABORTED_VOID','operation_id':key}
                        cursor.setinputsizes(result=oracledb.DB_TYPE_BLOB)
                        try:
                            cursor.execute('INSERT INTO AP_PLAN_KEY (OPERATION_ID,ACTOR_ID,OPERATION_KIND,STATUS,RESULT_BLOB,RECORDED_AT) VALUES (:key,:actor,\'verify\',\'ABORTED_VOID\',:result,:now)',key=key,actor=actor,result=encode(result),now=now)
                        except oracledb.IntegrityError as error:
                            if code(error)!=1: raise
                            connection.rollback()
                            self.authorize(cursor,actor,True)
                            result=self._key(cursor,key,actor)
                            if result is None: raise StoreUnavailable
                            return result
                    committing=True; connection.commit()
                    return result
                except BaseException:
                    connection.rollback(); raise
        except oracledb.Error:
            # An insert may have waited on a save reservation. Never claim void
            # until its commit is acknowledged or the durable winner is read.
            raise PlanUnknown from None

    def write_context_in_transaction(self,cursor,actor,now,context,transform_inputs=None):
        context=PlanningContext.model_validate(context)
        def advance(current):
            candidate=transform_inputs(current) if transform_inputs else current
            return candidate.model_copy(update={'input_version':current.input_version+1})
        inputs=self.input_store.update_in_transaction(cursor,actor,now,advance)
        raw=document(context.model_dump(mode='json'))
        cursor.setinputsizes(blob=oracledb.DB_TYPE_BLOB)
        cursor.execute('INSERT INTO AP_PLAN_CONTEXT (INPUT_VERSION,CONTEXT_BLOB,SHA256,ACTOR_ID,RECORDED_AT) VALUES (:version,:blob,:sha,:actor,:now)',version=inputs.input_version,blob=zlib.compress(raw),sha=hashlib.sha256(raw).hexdigest(),actor=actor,now=now)
        return inputs

    def initialize_in_transaction(self,cursor,actor,now,inputs,context,plan,lineage,operation_id):
        """Caller owns key reservation and sole commit. Import creates pending draft."""
        head=self.head(cursor,True)
        if head!=(0,0): raise PlanProblem(409,'已有共享计划，不能重复首次接入')
        context=PlanningContext.model_validate(context).model_copy(update={'lineage':lineage})
        def initial(current):
            return InputSnapshot.model_validate(inputs).model_copy(update={'input_version':current.input_version+1})
        saved=self.write_context_in_transaction(cursor,actor,now,context,initial)
        unit=OraclePlanUnit(self,cursor,head,saved,context,str(operation_id))
        from .plans import PlanService, PlanCommand
        command=PlanCommand(base_revision=0,input_version=saved.input_version,latest_adoption=0,operations=[{'kind':'replace_plan','plan':Plan.model_validate(plan).model_dump(mode='json')}])
        prepared=PlanService(self)._prepare(unit,command,now)
        return unit.append(prepared,command.operations,actor,now,'initialize',True)

    def check_consistency(self):
        """Read-only startup check; caller refuses writes if it raises."""
        try:
            with self.pool.acquire() as connection:
                try:
                    with connection.cursor() as cursor:
                        head=self.head(cursor)
                        for table,column,expected in [('AP_PLAN_REVISION','REVISION_NO',head[0]),('AP_PLAN_ADOPTION','ADOPTION_NO',head[1])]:
                            cursor.execute(f'SELECT NVL(MAX({column}),0) FROM {table}')
                            if int(cursor.fetchone()[0])!=expected: raise StoreUnavailable
                        cursor.execute('SELECT R.REVISION_NO,R.INPUT_VERSION,S.SNAPSHOT_BLOB,S.SHA256 FROM AP_PLAN_REVISION R JOIN AP_PLAN_SNAPSHOT S ON S.SHA256=R.SNAPSHOT_SHA')
                        for revision,version,blob,sha in cursor:
                            record=decode(blob,sha)
                            if record.get('revision')!=revision or record.get('input_version')!=version or record['snapshot']['inputs']['input_version']!=version:
                                raise StoreUnavailable
                        cursor.execute('SELECT A.ADOPTION_NO,S.SNAPSHOT_BLOB,S.SHA256 FROM AP_PLAN_ADOPTION A JOIN AP_PLAN_SNAPSHOT S ON S.SHA256=A.SNAPSHOT_SHA')
                        for adoption,blob,sha in cursor:
                            record=decode(blob,sha)
                            if record.get('latest_adoption')!=adoption or record.get('kind')!='adopt' or not record['evaluation']['executable']:
                                raise StoreUnavailable
                            with connection.cursor() as details:
                                details.execute('SELECT PRODUCTION_DATE,LINE_CODE,MODEL_CODE,QUANTITY FROM AP_ADOPTED_CELL WHERE ADOPTION_NO=:adoption',adoption=adoption)
                                adopted={(day.date().isoformat(),line,model):int(quantity) for day,line,model,quantity in details}
                            if adopted!=cells(Plan.model_validate(record['snapshot']['plan'])): raise StoreUnavailable
                        cursor.execute('SELECT K.OPERATION_ID,K.STATUS,K.RESULT_BLOB,A.REVISION_NO FROM AP_PLAN_KEY K LEFT JOIN AP_PLAN_AUDIT A ON A.OPERATION_ID=K.OPERATION_ID')
                        for key,status,blob,revision in cursor:
                            result=decode(blob)
                            if status=='COMMITTED':
                                if result.get('status')!='COMMITTED' or result.get('revision')!=revision or not revision or revision>head[0]: raise StoreUnavailable
                            elif status=='ABORTED_VOID':
                                if result.get('status')!='ABORTED_VOID' or revision is not None or 'revision' in result: raise StoreUnavailable
                            else: raise StoreUnavailable
                        cursor.execute('SELECT COUNT(*) FROM AP_PLAN_ADOPTION A LEFT JOIN AP_PLAN_REVISION R ON R.REVISION_NO=A.REVISION_NO WHERE R.REVISION_NO IS NULL OR R.SNAPSHOT_SHA<>A.SNAPSHOT_SHA OR R.OPERATION_KIND<>\'adopt\'')
                        if cursor.fetchone()[0]: raise StoreUnavailable
                    return True
                finally: connection.rollback()
        except oracledb.Error: raise StoreUnavailable from None
