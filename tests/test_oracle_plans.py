"""Real PLAN_TEST public API transaction and connection-fault evidence."""
import os
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event, Lock
from time import monotonic
from uuid import uuid4
import oracledb
import pytest
from fastapi.testclient import TestClient
from auto_plan.app import create_app
from auto_plan.settings import Settings
from auto_plan.oracle_access import OracleAccessStore
from auto_plan.oracle_plans import OraclePlanStore
from auto_plan.plans import PlanningContext
from tests.support import CompanyAuthentication

pytestmark=[pytest.mark.oracle,pytest.mark.skipif(os.getenv('AUTO_PLAN_TEST_ORACLE')!='1',reason='Opt-in PLAN_TEST AP_* only')]
NOW=datetime(2026,10,14,tzinfo=timezone.utc)


class FaultPool:
    """Real database connection wrapper for pauses and transport acknowledgement loss."""
    def __init__(self,pool): self.pool=pool; self.before_execute=None; self.after_execute=None; self.commit_fault=None; self.timeout_override=None
    @contextmanager
    def acquire(self):
        with self.pool.acquire() as connection:
            yield FaultConnection(connection,self)


class FaultConnection:
    def __init__(self,connection,owner): self.connection=connection; self.owner=owner
    @property
    def call_timeout(self): return self.connection.call_timeout
    @call_timeout.setter
    def call_timeout(self,value): self.connection.call_timeout=self.owner.timeout_override or value
    @contextmanager
    def cursor(self):
        with self.connection.cursor() as cursor: yield FaultCursor(cursor,self.owner)
    def rollback(self): return self.connection.rollback()
    def commit(self):
        fault=self.owner.commit_fault
        if fault:
            self.owner.commit_fault=None
            if fault in ('after','drop_after'): self.connection.commit()
            if fault in ('drop_before','drop_after'): self.connection.close()
            raise oracledb.OperationalError('Simulated acknowledgement loss at the connection boundary')
        return self.connection.commit()


class FaultCursor:
    def __init__(self,cursor,owner): self.cursor=cursor; self.owner=owner
    def __getattr__(self,name): return getattr(self.cursor,name)
    def __iter__(self): return iter(self.cursor)
    def execute(self,statement,*args,**kwargs):
        before=self.owner.before_execute
        if before: before(statement,kwargs)
        value=self.cursor.execute(statement,*args,**kwargs)
        hook=self.owner.after_execute
        if hook: hook(statement,kwargs)
        return value


@pytest.fixture(scope='module')
def real_app():
    settings=Settings(initial_admin_id='T24ADMIN')
    assert settings.oracle_user.upper()=='PLAN_TEST'
    access=OracleAccessStore.connect(settings)
    faults=FaultPool(access.pool); plans=OraclePlanStore(faults)
    app=create_app(settings,store=access,plan_store=plans,authenticator=CompanyAuthentication(),clock=lambda:NOW)
    with TestClient(app,base_url=settings.public_origin) as client:
        client.headers.update({'Origin':settings.public_origin,'X-Auto-Plan-Request':'1'})
        assert client.post('/auto_plan/api/auth/login',json={'username':'T24ADMIN','password':'company-secret'}).status_code==200
        assert client.put('/auto_plan/api/admin/authorizations/T29EDITOR',json={'roles':['editor']}).status_code==200
        assert client.post('/auto_plan/api/auth/login',json={'username':'T29EDITOR','password':'company-secret'}).status_code==200
        suffix=uuid4().hex[:8].upper(); recipe='T29R'+suffix; model='T29M'+suffix; other='T29N'+suffix; line='T29L'+suffix; line2='T29B'+suffix
        current=client.get('/auto_plan/api/inputs').json()
        changes={'recipes':[{'code':recipe,'name':'事务测试','active':True}],
                 'models':[{'code':m,'name':m,'active':True,'recipe_code':recipe} for m in (model,other)],
                 'lines':[{'code':l,'name':l,'active':True} for l in (line,line2)],
                 'capacities':[{'month':'2026-10','recipe_code':recipe,'line_code':l,'pieces':1000} for l in (line,line2)],
                 'line_yields':[{'month':'2026-10','percent':'100'}],
                 'model_yields':[{'month':'2026-10','model_code':m,'percent':'100'} for m in (model,other)],
                 'cycle_times':[{'model_code':m,'days':'0'} for m in (model,other)]}
        response=client.post('/auto_plan/api/inputs/apply',json={'base_version':current['input_version'],'changes':changes})
        assert response.status_code==200,response.text
        yield client,access,plans,faults,model,other,line,line2
    access.close()


def current(client):
    response=client.get('/auto_plan/api/plans/current'); assert response.status_code==200,response.text
    return response.json()
def body(client,operations=None,**extra):
    head=current(client)
    return {'base_revision':head['revision'],'input_version':head['input_version'],'latest_adoption':head['latest_adoption'],'operations':operations or [],'operation_id':str(uuid4()),'pending':True}|extra
def post(client,path,payload): return client.post('/auto_plan/api/plans/'+path,json=payload)
def seg(identity,model,line,day='2026-10-15',quantity=100):
    return {'id':identity,'date':day,'line_code':line,'order':0,'model_code':model,'quantity':quantity,'hours':'3'}
def seed(real_app):
    client,_,_,_,model,_,line,_=real_app
    response=post(client,'save',body(client,[{'kind':'replace_plan','plan':{'segments':[seg('initial',model,line)]}}]))
    assert response.status_code==200,response.text
    return current(client)


def test_real_adoption_race_same_key_replay_and_frozen_adopted_cells(real_app):
    client,access,_,_,model,_,line,_=real_app
    seed(real_app)
    first=body(client); second=first|{'operation_id':str(uuid4())}
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda b:post(client,'adopt',b),[first,second]))
    assert sorted(r.status_code for r in results)==[200,409]
    winner=next(r.json() for r in results if r.status_code==200)
    winning_body=first if results[0].status_code==200 else second
    assert post(client,'adopt',winning_body).json()==winner
    assert post(client,'verify',{'operation_id':winning_body['operation_id']}).json()==winner
    history=client.get(f"/auto_plan/api/plans/adoptions/{winner['latest_adoption']}").json()
    assert history['snapshot']['plan']['segments'][0]['model_code']==model
    # Trigger behavior is database-specific evidence; API offers no mutation route.
    with access.pool.acquire() as connection:
        with connection.cursor() as cursor:
            for table,column,identity in [('AP_PLAN_ADOPTION','ADOPTION_NO',winner['latest_adoption']),('AP_ADOPTED_CELL','ADOPTION_NO',winner['latest_adoption']),('AP_PLAN_AUDIT','REVISION_NO',winner['revision']),('AP_PLAN_SNAPSHOT','SHA256',None)]:
                if identity is None:
                    cursor.execute('SELECT SNAPSHOT_SHA FROM AP_PLAN_ADOPTION WHERE ADOPTION_NO=:n',n=winner['latest_adoption']); identity=cursor.fetchone()[0]
                for verb in ('UPDATE','DELETE'):
                    statement=f'UPDATE {table} SET {column}={column} WHERE {column}=:n' if verb=='UPDATE' else f'DELETE FROM {table} WHERE {column}=:n'
                    with pytest.raises(oracledb.DatabaseError) as error: cursor.execute(statement,n=identity)
                    assert error.value.args[0].code==20029
                    connection.rollback()
    assert client.get(f"/auto_plan/api/plans/adoptions/{winner['latest_adoption']}").json()==history
    assert real_app[2].check_consistency() is True


def test_real_stale_independent_merge_and_cross_line_source_conflict(real_app):
    client,_,_,_,model,other,line,line2=real_app
    seed(real_app); baseline=body(client)
    first=baseline|{'operations':[{'kind':'patch_plan','segments':[seg('a',model,line,'2026-10-16')]}]}
    independent=baseline|{'operation_id':str(uuid4()),'operations':[{'kind':'patch_plan','segments':[seg('b',other,line,'2026-10-17')]}]}
    assert post(client,'save',first).status_code==200
    merged=post(client,'save',independent)
    assert merged.status_code==200,merged.text
    baseline=body(client)
    first=baseline|{'operations':[{'kind':'patch_plan','segments':[seg('c',model,line,'2026-10-18')]}]}
    dependent=baseline|{'operation_id':str(uuid4()),'operations':[{'kind':'patch_plan','segments':[seg('d',model,line2,'2026-10-18')]}]}
    assert post(client,'save',first).status_code==200
    rejected=post(client,'save',dependent)
    assert rejected.status_code==409
    assert ['CELL','2026-10-18',line2,model] in rejected.json()['conflicts']


@pytest.mark.parametrize('commit_save',[True,False])
def test_real_verifier_waits_for_uncommitted_save_reservation_then_reads_commit_or_void(real_app,commit_save):
    client,_,_,faults,_,_,_,_=real_app
    seed(real_app); request=body(client)
    inserted,release,verification_entered=Event(),Event(),Event()
    def pause(statement,bindings):
        if statement.startswith('INSERT INTO AP_PLAN_KEY') and bindings.get('key')==request['operation_id'] and bindings.get('kind')=='save':
            inserted.set(); assert release.wait(10)
    faults.after_execute=pause
    def entering(statement,bindings):
        if statement.startswith('INSERT INTO AP_PLAN_KEY') and bindings.get('key')==request['operation_id'] and bindings.get('kind') is None:
            verification_entered.set()
    faults.before_execute=entering
    try:
        with ThreadPoolExecutor(2) as pool:
            save=pool.submit(lambda:post(client,'save',request))
            assert inserted.wait(5)
            if not commit_save: faults.commit_fault='before'
            verification=pool.submit(lambda:post(client,'verify',{'operation_id':request['operation_id']}))
            assert verification_entered.wait(5)
            assert not verification.done()
            release.set()
            saved,verified=save.result(20),verification.result(20)
        assert verified.status_code==200,verified.text
        assert verified.json()['status']==('COMMITTED' if commit_save else 'ABORTED_VOID')
        assert saved.status_code==(200 if commit_save else 503)
        late=post(client,'save',request)
        assert late.status_code==(200 if commit_save else 409)
    finally: release.set(); faults.after_execute=None; faults.before_execute=None; faults.commit_fault=None


def test_real_void_before_save_and_commit_acknowledgement_loss_do_not_duplicate(real_app):
    client,_,_,faults,_,_,_,_=real_app
    seed(real_app); request=body(client)
    void=post(client,'verify',{'operation_id':request['operation_id']})
    assert void.status_code==200 and void.json()['status']=='ABORTED_VOID'
    previous=current(client)
    assert post(client,'save',request).status_code==409
    assert current(client)['revision']==previous['revision']
    for mode,expected in [('before','ABORTED_VOID'),('after','COMMITTED'),('drop_before','ABORTED_VOID'),('drop_after','COMMITTED')]:
        request=body(client)
        faults.commit_fault=mode
        response=post(client,'save',request)
        assert response.status_code==503 and response.json()['state']=='unknown'
        verified=post(client,'verify',{'operation_id':request['operation_id']})
        assert verified.status_code==200 and verified.json()['status']==expected
        count=current(client)['revision']
        repeated=post(client,'save',request)
        assert repeated.status_code==(200 if mode in ('after','drop_after') else 409)
        assert current(client)['revision']==count


def test_real_head_wait10_rolls_back_without_key_or_revision(real_app):
    client,access,_,_,_,_,_,_=real_app
    seed(real_app); request=body(client); before=current(client)['revision']
    with access.pool.acquire() as blocker:
        with blocker.cursor() as cursor:
            cursor.execute('SELECT ID FROM AP_PLAN_HEAD WHERE ID=1 FOR UPDATE WAIT 10')
            started=monotonic(); response=post(client,'save',request); elapsed=monotonic()-started
            assert response.status_code==503 and response.json()['state']=='busy'
            assert 9.5<=elapsed<=14
        blocker.rollback()
    assert current(client)['revision']==before
    assert post(client,'verify',{'operation_id':request['operation_id']}).json()['status']=='ABORTED_VOID'


def test_real_context_inherits_through_parameter_revision_and_input_guard_blocks_save(real_app):
    client,access,plans,_,model,_,line,_=real_app
    seed(real_app)
    with access.pool.acquire() as connection:
        with connection.cursor() as cursor:
            plans.head(cursor,True)
            context=PlanningContext.model_validate({'locks':[{'date':'2026-10-16','line_code':line,'model_code':model,'quantity':0}],'lineage':{'initial_coverage':'confirmed','external_reset_week':'2026-10-12'}})
            plans.write_context_in_transaction(cursor,'T29EDITOR',NOW,context)
        connection.commit()
    context_before=current(client)['context']
    version=current(client)['input_version']
    changed=client.post('/auto_plan/api/inputs/apply',json={'base_version':version,'changes':{'cycle_times':[{'model_code':model,'days':'0.5'}]}})
    assert changed.status_code==200,changed.text
    assert current(client)['context']==context_before
    stale=body(client)
    with access.pool.acquire() as blocker:
        with blocker.cursor() as cursor:
            cursor.execute('SELECT CURRENT_VERSION FROM AP_INPUT_HEAD WHERE ID=1 FOR UPDATE WAIT 10')
            with ThreadPoolExecutor(1) as pool:
                future=pool.submit(lambda:post(client,'save',stale))
                # A guarded input writer publishes a new complete token before save checks.
                plans.write_context_in_transaction(cursor,'T29EDITOR',NOW,context.model_copy(update={'lineage':{'guarded_update':True}}))
                blocker.commit()
                rejected=future.result(20)
    assert rejected.status_code==409
    assert rejected.json()['input_version']>stale['input_version']
    # Restore context through another guarded version; no old immutable row is modified.
    with access.pool.acquire() as connection:
        with connection.cursor() as cursor:
            plans.head(cursor,True); plans.write_context_in_transaction(cursor,'T29EDITOR',NOW,PlanningContext())
        connection.commit()


def test_real_fresh_namespace_initialization_is_one_atomic_input_context_draft_commit(real_app):
    from tests.oracle_namespace import disposable_business_namespace
    from tests.test_plan_preview import snapshot
    from auto_plan.input_models import InputSnapshot
    from auto_plan.plans import PlanService, InitializePlan, PlanProblem, PlanUnknown
    client,access,_,_,_,_,_,_=real_app
    with disposable_business_namespace(access.pool,'AP_TEST_'+uuid4().hex[:6].upper()) as namespace:
        faults=FaultPool(namespace); store=OraclePlanStore(faults); service=PlanService(store)
        source=snapshot(); plan=source['plan']
        for row in plan['segments']: row['date']='2026-10-15'
        context=PlanningContext.model_validate({'coverage':[{'start_date':'2026-10-12','end_date':'2027-01-24','line_codes':['L'],'model_codes':['M','N'],'blanks_are_zero':True}],'lineage':{'external_reset_week':'2026-10-12'}})
        request=InitializePlan(operation_id=uuid4(),base_revision=0,input_version=0,latest_adoption=0,pending=True,inputs=InputSnapshot.model_validate(source['inputs']),context=context,plan=plan,lineage=context.lineage)
        # Failure after the new input/context writes must roll both back too.
        locked=PlanningContext.model_validate(context.model_dump(mode='json')|{'locks':[{'date':'2026-10-15','line_code':'L','model_code':'M','quantity':0}]})
        invalid=request.model_copy(update={'operation_id':uuid4(),'context':locked})
        with pytest.raises(PlanProblem): service.initialize(invalid,'T29EDITOR',NOW)
        untouched=service.current('T29EDITOR',NOW)
        assert (untouched['revision'],untouched['input_version'],untouched['latest_adoption'])==(0,0,0)
        assert untouched['context']['coverage']==[]
        assert service.verify(invalid.operation_id,'T29EDITOR',NOW)['status']=='ABORTED_VOID'
        faults.commit_fault='before'
        failed=request.model_copy(update={'operation_id':uuid4()})
        with pytest.raises(PlanUnknown): service.initialize(failed,'T29EDITOR',NOW)
        assert service.current('T29EDITOR',NOW)['input_version']==0
        assert service.verify(failed.operation_id,'T29EDITOR',NOW)['status']=='ABORTED_VOID'
        faults.commit_fault='after'
        with pytest.raises(PlanUnknown): service.initialize(request,'T29EDITOR',NOW)
        committed=service.verify(request.operation_id,'T29EDITOR',NOW)
        assert committed['status']=='COMMITTED'
        head=service.current('T29EDITOR',NOW)
        assert (head['revision'],head['input_version'],head['latest_adoption'])==(1,1,0)
        assert head['context']['coverage']==context.model_dump(mode='json')['coverage']
        assert head['context']['lineage']==context.lineage
        assert service.initialize(request,'T29EDITOR',NOW)==committed
        assert service.revision(1,'T29EDITOR')['coverage']==context.model_dump(mode='json')['coverage']
        assert service.revision(1,'T29EDITOR')['lineage']==context.lineage
        assert service.current('T29EDITOR',NOW)['revision']==1
        assert store.check_consistency() is True
        with namespace.acquire() as connection:
            with connection.cursor() as cursor:
                cursor.execute('UPDATE AP_PLAN_HEAD SET CURRENT_REVISION=999 WHERE ID=1')
            connection.commit()
        from auto_plan.access import StoreUnavailable
        with pytest.raises(StoreUnavailable): store.check_consistency()


def test_real_quota_dependency_conflicts_across_lines_and_dates(real_app):
    client,access,plans,_,model,_,line,line2=real_app
    seed(real_app)
    # Demand is maintained by #26 later; compose its typed totals through the
    # already public guarded InputStore contract for this real transaction test.
    from auto_plan.input_models import InputSnapshot
    from auto_plan.oracle_inputs import OracleInputStore
    inputs=OracleInputStore(access.pool)
    def demand(current):
        value=current.model_dump(mode='json')
        value['business']['demands']=[r for r in value['business']['demands'] if r['model_code']!=model]+[{'model_code':model,'week':'2026-10-19','quantity':500}]
        value['input_version']+=1
        return InputSnapshot.model_validate(value)
    inputs.update('T29EDITOR',NOW,demand)
    baseline=body(client)
    first=baseline|{'operations':[{'kind':'patch_plan','segments':[seg('quota-a',model,line,'2026-10-16')]}]}
    second=baseline|{'operation_id':str(uuid4()),'operations':[{'kind':'patch_plan','segments':[seg('quota-b',model,line2,'2026-10-17')]}]}
    assert post(client,'save',first).status_code==200
    rejected=post(client,'save',second)
    assert rejected.status_code==409
    assert ['REQ',model,'2026-10-19'] in rejected.json()['conflicts']


def test_real_editor_revoked_while_request_waits_is_rechecked_before_writing(real_app):
    client,access,_,faults,_,_,_,_=real_app
    seed(real_app); request=body(client); before=current(client)['revision']
    inserted,release=Event(),Event()
    def pause(statement,bindings):
        if statement.startswith('INSERT INTO AP_PLAN_KEY') and bindings.get('key')==request['operation_id']:
            inserted.set(); assert release.wait(10)
    faults.after_execute=pause
    try:
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(lambda:post(client,'save',request))
            assert inserted.wait(5)
            access.authorize('T24ADMIN','T29EDITOR',frozenset({'viewer'}),True,NOW)
            release.set(); response=future.result(20)
        assert response.status_code==403
        assert current(client)['revision']==before
    finally:
        release.set(); faults.after_execute=None
        access.authorize('T24ADMIN','T29EDITOR',frozenset({'editor'}),True,NOW)
    assert post(client,'verify',{'operation_id':request['operation_id']}).json()['status']=='ABORTED_VOID'


@pytest.mark.parametrize('fault',['before','after'])
def test_real_void_commit_acknowledgement_failure_stays_unknown_until_verified(real_app,fault):
    client,_,_,faults,_,_,_,_=real_app
    request=body(client)
    faults.commit_fault=fault
    first=post(client,'verify',{'operation_id':request['operation_id']})
    assert first.status_code==503 and first.json()['state']=='unknown'
    resolved=post(client,'verify',{'operation_id':request['operation_id']})
    assert resolved.status_code==200 and resolved.json()['status']=='ABORTED_VOID'
    assert post(client,'save',request).status_code==409


def test_real_unique_insert_timeout_returns_unknown_without_false_void(real_app):
    client,_,_,faults,_,_,_,_=real_app
    seed(real_app); request=body(client)
    inserted,release=Event(),Event()
    def pause(statement,bindings):
        if statement.startswith('INSERT INTO AP_PLAN_KEY') and bindings.get('key')==request['operation_id'] and bindings.get('kind')=='save':
            inserted.set(); assert release.wait(10)
    faults.after_execute=pause
    try:
        with ThreadPoolExecutor(1) as pool:
            saving=pool.submit(lambda:post(client,'save',request))
            assert inserted.wait(5)
            # Bound only this app verifier's real blocked Oracle round trip.
            faults.timeout_override=300
            unknown=post(client,'verify',{'operation_id':request['operation_id']})
            faults.timeout_override=None
            assert unknown.status_code==503 and unknown.json()['state']=='unknown'
            release.set(); saved=saving.result(20)
        assert saved.status_code==200,saved.text
        resolved=post(client,'verify',{'operation_id':request['operation_id']})
        assert resolved.status_code==200 and resolved.json()==saved.json()
    finally:
        release.set(); faults.timeout_override=None; faults.after_execute=None
