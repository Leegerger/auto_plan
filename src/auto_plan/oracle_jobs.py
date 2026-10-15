"""Oracle owns admission, FIFO, run fences and complete retained candidates."""
from datetime import timedelta, timezone
from uuid import uuid4
import oracledb
from .access import AuthorizationDenied, StoreUnavailable
from .jobs import JobUnknown
from .plans import PlanProblem, PlanBusy
from .oracle_plans import OraclePlanStore, code
from .solver_models import FrozenSolveInput, Candidate
from .job_codec import encode, decode, digest

RETENTION = timedelta(days=30)
TERMINAL = {'completed', 'stopped', 'interrupted', 'failed'}


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def retained_candidate(cursor, job_id, candidate_id, sha, now, lock=False):
    cursor.execute('SELECT PAYLOAD_BLOB,PAYLOAD_SHA,LAST_VIEWED_AT,EXPIRED_AT FROM AP_JOB_CANDIDATE WHERE JOB_ID=:job AND CANDIDATE_ID=:candidate'+(' FOR UPDATE WAIT 10' if lock else ''), job=job_id,candidate=candidate_id)
    row=cursor.fetchone()
    if not row: raise KeyError(candidate_id)
    if row[3] is not None or row[0] is None or utc(row[2])+RETENTION<=now:
        raise PlanProblem(410,'候选内容已过期；可按原始输入重新计算',candidate_id=candidate_id)
    if sha is not None and row[1]!=sha:
        raise PlanProblem(409,'候选引用与内容摘要不一致')
    return Candidate.model_validate(decode(row[0],row[1])),row[1]


class OracleJobStore:
    def __init__(self,pool,queue_capacity=20,worker_limit=1):
        self.pool=pool
        self.queue_capacity,self.worker_limit=queue_capacity,worker_limit
        self.plans=OraclePlanStore(pool)
        self.generation=None
        self.pending_claim=None

    def transaction(self,action,*,uncertain=False):
        commit_attempted=False
        commit_acknowledged=False
        rollback_acknowledged=False
        result=None
        try:
            with self.pool.acquire() as connection:
                connection.call_timeout=2000
                try:
                    with connection.cursor() as cursor:
                        result=action(cursor)
                    commit_attempted=True
                    connection.commit()
                    commit_acknowledged=True
                    return result
                except BaseException:
                    if not commit_acknowledged:
                        try:
                            connection.rollback()
                            rollback_acknowledged=True
                        except oracledb.Error:
                            # Discard an unusable production connection. A failed
                            # rollback never resolves the earlier transaction.
                            discard=getattr(self.pool,'drop',None)
                            if discard:
                                try: discard(connection)
                                except oracledb.Error: pass
                            raise JobUnknown from None
                    raise
        except oracledb.Error as error:
            if commit_acknowledged:
                # A pool-release failure cannot erase an acknowledged commit.
                return result
            if commit_attempted or uncertain or not rollback_acknowledged:
                raise JobUnknown from None
            if code(error) in (30006,54): raise PlanBusy from None
            raise StoreUnavailable from None

    @staticmethod
    def head(cursor):
        cursor.execute('SELECT NEXT_FIFO,GENERATION FROM AP_JOB_HEAD WHERE ID=1 FOR UPDATE WAIT 10')
        row=cursor.fetchone()
        if not row: raise StoreUnavailable
        return int(row[0]),row[1]

    @staticmethod
    def authorized(cursor,actor,editor=False):
        OraclePlanStore.authorize(cursor,actor,editor)

    @staticmethod
    def editor(cursor,actor):
        cursor.execute('SELECT EDITOR,ACTIVE FROM AP_AUTHORIZATION WHERE USER_ID=:actor',actor=actor)
        row=cursor.fetchone()
        return bool(row and row[0] and row[1])

    def _key(self,cursor,key,actor,sha=None):
        cursor.execute('SELECT ACTOR_ID,REQUEST_SHA,STATUS,RESULT_BLOB FROM AP_JOB_KEY WHERE SUBMISSION_ID=:key',key=key)
        row=cursor.fetchone()
        if not row: return None
        if row[0]!=actor or sha is not None and (row[1]!=sha or row[2]=='ABORTED_VOID'):
            raise PlanProblem(409,'提交编号已用于其他请求或已作废，请核实原请求')
        return decode(row[3])

    def _admit(self,cursor,command,actor,now,freeze,sha):
        self.authorized(cursor,actor,True)
        key=str(command.submission_id)
        prior=self._key(cursor,key,actor,sha)
        if prior is not None: return prior
        fifo,_=self.head(cursor)
        self.authorized(cursor,actor,True)
        prior=self._key(cursor,key,actor,sha)
        if prior is not None: return prior
        cursor.execute("SELECT COUNT(*) FROM AP_JOB WHERE STATE='queued'")
        if int(cursor.fetchone()[0])>=self.queue_capacity:
            raise PlanProblem(429,'计算队列已满，请稍后重试',queue_capacity=self.queue_capacity)
        cursor.execute("SELECT COUNT(*) FROM AP_JOB WHERE ACTOR_ID=:actor AND STATE='queued'",actor=actor)
        if cursor.fetchone()[0]: raise PlanProblem(429,'每个账号最多一个运行和一个排队任务')
        if self.worker_limit<1: raise PlanProblem(503,'当前分配的 CPU 不足以运行计算 worker',state='unavailable')
        frozen=freeze(cursor)
        data=frozen.model_dump(mode='json')
        identity=str(uuid4())
        result={'status':'ACCEPTED','submission_id':key,'job_id':identity}
        cursor.setinputsizes(blob=oracledb.DB_TYPE_BLOB)
        cursor.execute("INSERT INTO AP_JOB (JOB_ID,ACTOR_ID,FIFO_NO,FROZEN_BLOB,FROZEN_SHA,SOURCE_REVISION,INPUT_VERSION,LATEST_ADOPTION,STATE,CREATED_AT) VALUES (:job,:actor,:fifo,:blob,:sha,:revision,:version,:adoption,'queued',:now)",job=identity,actor=actor,fifo=fifo,blob=encode(data),sha=digest(data),revision=frozen.source_revision,version=frozen.input_version,adoption=frozen.latest_adoption,now=now)
        cursor.execute('UPDATE AP_JOB_HEAD SET NEXT_FIFO=:fifo WHERE ID=1',fifo=fifo+1)
        cursor.setinputsizes(result=oracledb.DB_TYPE_BLOB)
        cursor.execute("INSERT INTO AP_JOB_KEY (SUBMISSION_ID,ACTOR_ID,REQUEST_SHA,STATUS,JOB_ID,RESULT_BLOB,RECORDED_AT) VALUES (:key,:actor,:sha,'ACCEPTED',:job,:result,:now)",key=key,actor=actor,sha=sha,job=identity,result=encode(result),now=now)
        return result

    def submit(self,command,actor,now,freeze):
        def capture(cursor):
            head=self.plans.head(cursor,True)
            unit=self.plans.unit(cursor,head,lock=True)
            return freeze(unit)
        return self.transaction(lambda cursor:self._admit(cursor,command,actor,now,capture,digest(command.model_dump(mode='json'))))

    def rerun(self,identity,command,actor,now):
        def capture(cursor):
            cursor.execute('SELECT FROZEN_BLOB,FROZEN_SHA FROM AP_JOB WHERE JOB_ID=:job',job=identity)
            row=cursor.fetchone()
            if not row: raise KeyError(identity)
            return FrozenSolveInput.model_validate(decode(row[0],row[1]))
        return self.transaction(lambda cursor:self._admit(cursor,command,actor,now,capture,digest({'rerun':identity,**command.model_dump(mode='json')})))

    def verify(self,key,actor,now):
        def action(cursor):
            self.authorized(cursor,actor,True)
            self.head(cursor)
            self.authorized(cursor,actor,True)
            prior=self._key(cursor,key,actor)
            if prior is not None: return prior
            result={'status':'ABORTED_VOID','submission_id':key}
            cursor.setinputsizes(result=oracledb.DB_TYPE_BLOB)
            cursor.execute("INSERT INTO AP_JOB_KEY (SUBMISSION_ID,ACTOR_ID,STATUS,RESULT_BLOB,RECORDED_AT) VALUES (:key,:actor,'ABORTED_VOID',:result,:now)",key=key,actor=actor,result=encode(result),now=now)
            return result
        return self.transaction(action,uncertain=True)

    def _get(self,cursor,identity,now):
        cursor.execute('SELECT JOB_ID,ACTOR_ID,FIFO_NO,SOURCE_REVISION,INPUT_VERSION,LATEST_ADOPTION,STATE,STOP_REQUESTED_AT,STOP_REASON,PROGRESS_BLOB,RESULT_BLOB,CREATED_AT,STARTED_AT,ENDED_AT FROM AP_JOB WHERE JOB_ID=:job',job=identity)
        row=cursor.fetchone()
        if not row: raise KeyError(identity)
        names=['job_id','actor','fifo','source_revision','input_version','latest_adoption','state','stop_requested_at','stop_reason','progress','result','created_at','started_at','ended_at']
        result=dict(zip(names,row))
        for field in ['stop_requested_at','created_at','started_at','ended_at']:
            result[field]=utc(result[field]).isoformat() if result[field] is not None else None
        for field in ['progress','result']:
            result[field]=decode(result[field]) if result[field] is not None else None
        cursor.execute('SELECT CANDIDATE_ID,PAYLOAD_SHA,LAST_VIEWED_AT,EXPIRED_AT FROM AP_JOB_CANDIDATE WHERE JOB_ID=:job AND OFFERED=1 ORDER BY PUBLISHED_AT,CANDIDATE_ID',job=identity)
        result['candidates']=[{'candidate_id':cid,'payload_sha':sha,'expired':expired is not None or utc(viewed)+RETENTION<=now} for cid,sha,viewed,expired in cursor]
        return result

    def list(self,actor,now):
        def action(cursor):
            self.authorized(cursor,actor)
            cursor.execute('SELECT JOB_ID FROM AP_JOB ORDER BY FIFO_NO DESC FETCH FIRST 100 ROWS ONLY')
            ids=[r[0] for r in cursor]
            return {'jobs':[self._get(cursor,j,now) for j in ids],'queue_capacity':self.queue_capacity,'worker_limit':self.worker_limit}
        return self.transaction(action)

    def get(self,identity,actor,now):
        def action(cursor):
            self.authorized(cursor,actor)
            return self._get(cursor,identity,now)
        return self.transaction(action)

    def candidate(self,identity,candidate,actor,now):
        def action(cursor):
            self.authorized(cursor,actor)
            content,sha=retained_candidate(cursor,identity,candidate,None,now,True)
            self.authorized(cursor,actor)
            cursor.execute('UPDATE AP_JOB_CANDIDATE SET LAST_VIEWED_AT=CASE WHEN LAST_VIEWED_AT<:now THEN :now ELSE LAST_VIEWED_AT END WHERE CANDIDATE_ID=:candidate',now=now,candidate=candidate)
            cursor.execute('SELECT CURRENT_VERSION FROM AP_INPUT_HEAD WHERE ID=1')
            current_version=int(cursor.fetchone()[0])
            return {'job_id':identity,'candidate_id':candidate,'payload_sha':sha,'candidate':content.model_dump(mode='json'),
                'validity':{'state':'requires_recheck' if current_version==content.input_version else 'view_only','current_input_version':current_version},
                'proposal':{'base_revision':content.source_revision,'input_version':content.input_version,'latest_adoption':content.latest_adoption,
                    'operations':[{'kind':'apply_candidate','job_id':identity,'candidate_id':candidate,'payload_sha':sha}]}}
        return self.transaction(action)

    @classmethod
    def revoke_in_transaction(cls,cursor,actor,now):
        cls.head(cursor)
        cursor.execute("UPDATE AP_JOB SET STATE='stopped',STOP_REQUESTED_AT=:now,STOP_REASON='authorization_revoked',ENDED_AT=:now WHERE ACTOR_ID=:actor AND STATE='queued'",now=now,actor=actor)
        cursor.execute("UPDATE AP_JOB SET STOP_REQUESTED_AT=COALESCE(STOP_REQUESTED_AT,:now),STOP_REASON='authorization_revoked' WHERE ACTOR_ID=:actor AND STATE='running'",now=now,actor=actor)

    def stop(self,identity,actor,now):
        def action(cursor):
            self.head(cursor)
            cursor.execute('SELECT ACTOR_ID,STATE FROM AP_JOB WHERE JOB_ID=:job FOR UPDATE WAIT 10',job=identity)
            row=cursor.fetchone()
            if not row: raise KeyError(identity)
            cursor.execute('SELECT EDITOR,VIEWER,ADMINISTRATOR,ACTIVE FROM AP_AUTHORIZATION WHERE USER_ID=:actor',actor=actor)
            grant=cursor.fetchone()
            if not grant or not grant[3] or not (grant[2] or row[0]==actor and (grant[0] or grant[1])):
                raise AuthorizationDenied
            if row[1]=='queued':
                cursor.execute("UPDATE AP_JOB SET STATE='stopped',STOP_REQUESTED_AT=:now,STOP_REASON='user',ENDED_AT=:now WHERE JOB_ID=:job",now=now,job=identity)
            elif row[1]=='running':
                cursor.execute("UPDATE AP_JOB SET STOP_REQUESTED_AT=COALESCE(STOP_REQUESTED_AT,:now),STOP_REASON=COALESCE(STOP_REASON,'user') WHERE JOB_ID=:job",now=now,job=identity)
            # Stop acknowledges only execution metadata, never candidate content.
            cursor.execute('SELECT STATE,STOP_REQUESTED_AT FROM AP_JOB WHERE JOB_ID=:job',job=identity)
            state,stopped=cursor.fetchone()
            return {'job_id':identity,'state':state,'stop_requested_at':utc(stopped).isoformat() if stopped else None}
        return self.transaction(action)

    def recover_startup(self,now):
        self.check_consistency()
        generation=str(uuid4())
        def action(cursor):
            self.head(cursor)
            cursor.execute("UPDATE AP_JOB SET STATE='interrupted',ENDED_AT=:now,STOP_REASON=COALESCE(STOP_REASON,'restart') WHERE STATE='running'",now=now)
            cursor.execute('UPDATE AP_JOB_HEAD SET GENERATION=:generation WHERE ID=1',generation=generation)
            return generation
        self.generation=self.transaction(action)
        return self.generation

    def claim(self,now):
        if self.pending_claim is not None:
            return self._resolve_claim()
        def action(cursor):
            _,generation=self.head(cursor)
            if generation!=self.generation or not generation: return None
            # Recovery check independent of browser sessions.
            cursor.execute("SELECT DISTINCT J.ACTOR_ID FROM AP_JOB J LEFT JOIN AP_AUTHORIZATION A ON A.USER_ID=J.ACTOR_ID WHERE J.STATE IN ('queued','running') AND (A.USER_ID IS NULL OR A.ACTIVE=0 OR A.EDITOR=0)")
            revoked=[r[0] for r in cursor]
            for actor in revoked:
                self.revoke_in_transaction(cursor,actor,now)
            cursor.execute("SELECT COUNT(*) FROM AP_JOB WHERE STATE='running'")
            if int(cursor.fetchone()[0])>=self.worker_limit: return None
            cursor.execute("SELECT JOB_ID,ACTOR_ID FROM AP_JOB WHERE STATE='queued' ORDER BY FIFO_NO FETCH FIRST 1 ROW ONLY")
            row=cursor.fetchone()
            if not row: return None
            identity,actor=row
            cursor.execute("SELECT COUNT(*) FROM AP_JOB WHERE ACTOR_ID=:actor AND STATE='running'",actor=actor)
            if cursor.fetchone()[0]: return None  # Strict FIFO, no bypass.
            cursor.execute('SELECT FROZEN_BLOB,FROZEN_SHA FROM AP_JOB WHERE JOB_ID=:job FOR UPDATE WAIT 10',job=identity)
            blob,sha=cursor.fetchone()
            frozen=FrozenSolveInput.model_validate(decode(blob,sha))
            run=str(uuid4())
            cursor.execute("UPDATE AP_JOB SET STATE='running',GENERATION=:generation,RUN_ID=:run,STARTED_AT=:now WHERE JOB_ID=:job",generation=generation,run=run,now=now,job=identity)
            self.pending_claim={'job_id':identity,'run_id':run,'generation':generation,'frozen':frozen}
            return self.pending_claim
        try:
            result=self.transaction(action)
            self.pending_claim=None
            return result
        except JobUnknown:
            if self.pending_claim is None: raise
            return self._resolve_claim()

    def _resolve_claim(self):
        pending=self.pending_claim
        def action(cursor):
            cursor.execute('SELECT STATE,RUN_ID,GENERATION FROM AP_JOB WHERE JOB_ID=:job',job=pending['job_id'])
            row=cursor.fetchone()
            return pending if row and tuple(row)==('running',pending['run_id'],pending['generation']) else None
        result=self.transaction(action,uncertain=True)
        self.pending_claim=None
        return result

    def control(self,identity,run,generation,now):
        def action(cursor):
            self.head(cursor)
            cursor.execute('SELECT ACTOR_ID,STATE,RUN_ID,GENERATION,STOP_REQUESTED_AT FROM AP_JOB WHERE JOB_ID=:job FOR UPDATE WAIT 10',job=identity)
            row=cursor.fetchone()
            if not row or row[1]!='running' or row[2]!=run or row[3]!=generation: return {'owned':False}
            if not self.editor(cursor,row[0]):
                self.revoke_in_transaction(cursor,row[0],now)
                return {'owned':True,'stop_requested_at':utc(row[4]) or now}
            return {'owned':True,'stop_requested_at':utc(row[4])}
        return self.transaction(action)

    def _owned(self,cursor,identity,run,generation):
        _,current=self.head(cursor)
        cursor.execute('SELECT STATE,RUN_ID,GENERATION,FROZEN_BLOB,FROZEN_SHA FROM AP_JOB WHERE JOB_ID=:job FOR UPDATE WAIT 10',job=identity)
        row=cursor.fetchone()
        if not row or row[0]!='running' or row[1]!=run or row[2]!=generation or current!=generation:
            raise PlanProblem(409,'计算进程已失去该任务的所有权')
        return FrozenSolveInput.model_validate(decode(row[3],row[4]))

    def publish(self,identity,run,generation,candidate,now):
        candidate=Candidate.model_validate(candidate)
        # Validate complete worker data before attempting a durable publication.
        from .plan_operations import OperationContext, PatchPlan, patch
        from .candidate_operations import validate_change
        from .rules import evaluate
        from .solver import objective
        from .plans import effect_keys
        context=OperationContext(candidate.snapshot)
        if patch(candidate.snapshot.plan,PatchPlan.model_validate(candidate.operation),context)!=candidate.plan:
            raise ValueError('Candidate patch does not reproduce its plan')
        validate_change(candidate.snapshot.plan,candidate.plan,candidate.request,context)
        evaluation=evaluate(candidate.snapshot.model_copy(update={'plan':candidate.plan}))
        if not evaluation.computation_complete or not evaluation.capacity_valid or any(issue.severity=='error' and issue.code!='confirmation_pending' for issue in evaluation.issues):
            raise ValueError('Incomplete or invalid candidate')
        if candidate.eligibility!=('executable' if evaluation.executable else 'pending_confirmation'):
            raise ValueError('Candidate eligibility mismatch')
        if evaluation!=candidate.evaluation or list(map(str,objective(evaluation)))!=candidate.objective or [list(k) for k in effect_keys(candidate.snapshot.plan,candidate.plan,candidate.snapshot)]!=candidate.effects:
            raise ValueError('Candidate evaluation or effects mismatch')
        data=candidate.model_dump(mode='json'); sha=digest(data)
        def action(cursor):
            frozen=self._owned(cursor,identity,run,generation)
            if (candidate.source_revision,candidate.input_version,candidate.latest_adoption,candidate.snapshot,candidate.request)!=(frozen.source_revision,frozen.input_version,frozen.latest_adoption,frozen.snapshot,frozen.request):
                raise ValueError('Worker candidate source mismatch')
            cursor.execute('SELECT CANDIDATE_ID FROM AP_JOB_CANDIDATE WHERE JOB_ID=:job AND PAYLOAD_SHA=:sha',job=identity,sha=sha)
            previous=cursor.fetchone()
            if previous: return {'candidate_id':previous[0],'payload_sha':sha}
            cid=str(uuid4())
            cursor.setinputsizes(blob=oracledb.DB_TYPE_BLOB)
            cursor.execute('INSERT INTO AP_JOB_CANDIDATE (CANDIDATE_ID,JOB_ID,PAYLOAD_SHA,PAYLOAD_BLOB,PUBLISHED_AT,LAST_VIEWED_AT) VALUES (:candidate,:job,:sha,:blob,:now,:now)',candidate=cid,job=identity,sha=sha,blob=encode(data),now=now)
            # Progress offers only the latest complete incumbent. Prior refs remain valid.
            cursor.execute('UPDATE AP_JOB_CANDIDATE SET OFFERED=0 WHERE JOB_ID=:job AND CANDIDATE_ID<>:candidate',job=identity,candidate=cid)
            return {'candidate_id':cid,'payload_sha':sha}
        return self.transaction(action)

    def progress(self,identity,run,generation,event,now):
        from .job_codec import document, METADATA_LIMIT
        if len(document(event))>METADATA_LIMIT: raise ValueError('Oversized progress metadata')
        def action(cursor):
            self._owned(cursor,identity,run,generation)
            cursor.setinputsizes(progress=oracledb.DB_TYPE_BLOB)
            cursor.execute('UPDATE AP_JOB SET PROGRESS_BLOB=:progress WHERE JOB_ID=:job',progress=encode(event),job=identity)
        return self.transaction(action)

    def finish(self,identity,run,generation,state,result,manifest,now):
        if state not in TERMINAL: raise ValueError('Invalid terminal state')
        def action(cursor):
            self.head(cursor)
            cursor.execute('SELECT STATE,RUN_ID,GENERATION,RESULT_BLOB FROM AP_JOB WHERE JOB_ID=:job FOR UPDATE WAIT 10',job=identity)
            previous=cursor.fetchone()
            if previous and previous[0] in TERMINAL:
                if previous[:3]==(state,run,generation) and previous[3] is not None and decode(previous[3])==result:
                    return True
                raise PlanProblem(409,'任务已结束，不能覆盖其持久结果')
            frozen=self._owned(cursor,identity,run,generation)
            if len(manifest)>frozen.request.max_candidates: raise ValueError('Too many offered candidates')
            if manifest:
                cursor.execute('SELECT PAYLOAD_SHA FROM AP_JOB_CANDIDATE WHERE JOB_ID=:job AND PAYLOAD_BLOB IS NOT NULL',job=identity)
                if set(manifest)-{r[0] for r in cursor}: raise ValueError('Uncommitted candidate manifest')
                cursor.execute('UPDATE AP_JOB_CANDIDATE SET OFFERED=0 WHERE JOB_ID=:job',job=identity)
                for sha in manifest:
                    cursor.execute('UPDATE AP_JOB_CANDIDATE SET OFFERED=1 WHERE JOB_ID=:job AND PAYLOAD_SHA=:sha',job=identity,sha=sha)
            cursor.setinputsizes(result=oracledb.DB_TYPE_BLOB)
            cursor.execute('UPDATE AP_JOB SET STATE=:state,RESULT_BLOB=:result,ENDED_AT=:now WHERE JOB_ID=:job',state=state,result=encode(result),now=now,job=identity)
            return True
        return self.transaction(action)

    def request_shutdown(self,identity,run,generation,now):
        def action(cursor):
            self._owned(cursor,identity,run,generation)
            cursor.execute("UPDATE AP_JOB SET STOP_REQUESTED_AT=COALESCE(STOP_REQUESTED_AT,:now),STOP_REASON=COALESCE(STOP_REASON,'shutdown') WHERE JOB_ID=:job",now=now,job=identity)
        return self.transaction(action)

    def cleanup(self,now):
        def action(cursor):
            self.head(cursor)
            cursor.execute('SELECT CANDIDATE_ID FROM AP_JOB_CANDIDATE WHERE PAYLOAD_BLOB IS NOT NULL AND LAST_VIEWED_AT<=:cutoff',cutoff=now-RETENTION)
            identities=[r[0] for r in cursor]
            count=0
            for identity in identities:
                cursor.execute('SELECT LAST_VIEWED_AT FROM AP_JOB_CANDIDATE WHERE CANDIDATE_ID=:candidate FOR UPDATE WAIT 10',candidate=identity)
                viewed=utc(cursor.fetchone()[0])
                if viewed+RETENTION<=now:
                    cursor.execute('UPDATE AP_JOB_CANDIDATE SET PAYLOAD_BLOB=NULL,OFFERED=0,EXPIRED_AT=:now WHERE CANDIDATE_ID=:candidate',now=now,candidate=identity)
                    count+=1
            return count
        return self.transaction(action)

    def check_consistency(self):
        def action(cursor):
            cursor.execute('SELECT JOB_ID,FROZEN_BLOB,FROZEN_SHA,SOURCE_REVISION,INPUT_VERSION,LATEST_ADOPTION FROM AP_JOB')
            jobs={}
            for identity,blob,sha,revision,version,adoption in cursor:
                frozen=FrozenSolveInput.model_validate(decode(blob,sha))
                if (frozen.source_revision,frozen.input_version,frozen.latest_adoption)!=(revision,version,adoption): raise StoreUnavailable
                jobs[identity]=frozen
            cursor.execute('SELECT COUNT(*) FROM AP_JOB J LEFT JOIN AP_PLAN_REVISION R ON R.REVISION_NO=J.SOURCE_REVISION LEFT JOIN AP_INPUT_REVISION I ON I.INPUT_VERSION=J.INPUT_VERSION LEFT JOIN AP_PLAN_ADOPTION A ON A.ADOPTION_NO=J.LATEST_ADOPTION WHERE (J.SOURCE_REVISION<>0 AND R.REVISION_NO IS NULL) OR (J.INPUT_VERSION<>0 AND I.INPUT_VERSION IS NULL) OR (J.LATEST_ADOPTION<>0 AND A.ADOPTION_NO IS NULL)')
            if cursor.fetchone()[0]: raise StoreUnavailable
            cursor.execute('SELECT NEXT_FIFO,(SELECT NVL(MAX(FIFO_NO),0) FROM AP_JOB) FROM AP_JOB_HEAD WHERE ID=1')
            head=cursor.fetchone()
            if head is None or int(head[0])<=int(head[1]): raise StoreUnavailable
            cursor.execute('SELECT JOB_ID FROM AP_JOB_KEY WHERE JOB_ID IS NOT NULL GROUP BY JOB_ID HAVING COUNT(*)<>1')
            if cursor.fetchone() is not None: raise StoreUnavailable
            cursor.execute('SELECT COUNT(*) FROM AP_JOB J LEFT JOIN AP_JOB_KEY K ON K.JOB_ID=J.JOB_ID WHERE K.SUBMISSION_ID IS NULL OR K.STATUS<>\'ACCEPTED\' OR K.ACTOR_ID<>J.ACTOR_ID')
            if cursor.fetchone()[0]: raise StoreUnavailable
            cursor.execute('SELECT JOB_ID,STATUS,RESULT_BLOB,SUBMISSION_ID FROM AP_JOB_KEY')
            for identity,state,blob,key in cursor:
                result=decode(blob)
                if result.get('status')!=state or result.get('submission_id')!=key or (state=='ACCEPTED' and (identity not in jobs or result.get('job_id')!=identity)) or (state=='ABORTED_VOID' and identity is not None): raise StoreUnavailable
            cursor.execute('SELECT JOB_ID,PAYLOAD_BLOB,PAYLOAD_SHA,OFFERED,PUBLISHED_AT,LAST_VIEWED_AT,EXPIRED_AT FROM AP_JOB_CANDIDATE')
            for identity,blob,sha,offered,published,viewed,expired in cursor:
                if identity not in jobs or utc(viewed)<utc(published): raise StoreUnavailable
                if (blob is None)!=(expired is not None) or blob is None and offered: raise StoreUnavailable
                if blob is not None:
                    candidate=Candidate.model_validate(decode(blob,sha)); frozen=jobs[identity]
                    if (candidate.snapshot,candidate.request,candidate.source_revision,candidate.input_version,candidate.latest_adoption)!=(frozen.snapshot,frozen.request,frozen.source_revision,frozen.input_version,frozen.latest_adoption): raise StoreUnavailable
            return True
        try: return self.transaction(action)
        except (ValueError,JobUnknown): raise StoreUnavailable from None
