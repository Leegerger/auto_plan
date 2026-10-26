"""Browser verification server. Explicitly inject test external boundaries only."""
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import uvicorn
from auto_plan.app import create_app
from auto_plan.settings import Settings
from tests.support import CompanyAuthentication, MemoryAccessStore
from tests.input_support import MemoryInputStore
from tests.plan_support import MemoryPlanStore
from tests.demand_support import MemoryDemandStore

if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='auto-plan-browser-') as directory:
        cert = Path(directory) / 'cert.pem'
        key = Path(directory) / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key), '-out', str(cert), '-days', '1', '-subj', '/CN=localhost'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port=int(os.getenv('AUTO_PLAN_BROWSER_PORT','8774'))
        settings = Settings(initial_admin_id='ADMIN', public_origin=f'https://localhost:{port}', base_path='/planning', frontend_dist=Path('frontend/dist'))
        access = MemoryAccessStore()
        inputs = MemoryInputStore()
        # Browser business fixtures use October 2026 dates, independent of wall time.
        default_plans = MemoryPlanStore(inputs)
        options = {'clock': lambda: datetime(2026, 10, 14, tzinfo=timezone.utc), 'plan_store': default_plans, 'demand_store': MemoryDemandStore(default_plans)}
        if os.getenv('AUTO_PLAN_BROWSER_OUTPUT_FIXTURE') == '1':
            from datetime import datetime, timezone
            from auto_plan.input_models import InputSnapshot
            from tests.test_output_preview import output_snapshot
            data = output_snapshot(1072, '2026-10-15')
            data['inputs']['business']['demands'] = [
                {'model_code': 'M', 'week': '2026-10-12', 'quantity': 600},
                {'model_code': 'M', 'week': '2026-10-19', 'quantity': 400}]
            inputs.versions[0] = InputSnapshot.model_validate(data['inputs'])
            access.users['OUTPUT_EDITOR'] = {'user_id': 'OUTPUT_EDITOR', 'roles': ['editor'], 'active': True}
            options['clock'] = lambda: datetime(2026, 10, 14, tzinfo=timezone.utc)
        if os.getenv('AUTO_PLAN_BROWSER_TRANSACTIONS_FIXTURE') == '1':
            from datetime import datetime, timezone
            from auto_plan.input_models import InputSnapshot
            from auto_plan.plans import PlanService, SubmitPlan
            from tests.plan_support import MemoryPlanStore
            from tests.test_plan_preview import snapshot
            from uuid import uuid4
            data = snapshot()['inputs'] | {'input_version':1}
            if os.getenv('AUTO_PLAN_BROWSER_EFFECTIVE_PREVIEW_FIXTURE') == '1':
                data['business'] = {'demands': [
                    {'model_code':'M','week':'2026-10-12','quantity':1000},
                    {'model_code':'P','week':'2026-10-12','quantity':500}]}
            if os.getenv('AUTO_PLAN_BROWSER_WORKSPACE_FIXTURE') == '1':
                from datetime import date, timedelta
                data['masters']['lines'] = [{'code':'L' if n==0 else f'L{n+1:02d}','name':f'线体 {n+1}','active':True} for n in range(18)]
                data['masters']['models'] += [{'code':f'X{n:02d}','name':f'机种 {n}','recipe_code':'R','active':True} for n in range(1,38)]
                months=['2026-10','2026-11','2026-12','2027-01']
                data['parameters']['capacities']=[{'month':month,'recipe_code':recipe,'line_code':line['code'],'pieces':1000} for month in months for recipe in ('R','S') for line in data['masters']['lines']]
                for category in ('line_yields','model_yields'):
                    data['parameters'][category]=[{'month':month,'model_code':model['code'],'percent':'100'} for month in months for model in data['masters']['models']]
                data['parameters']['cycle_times']=[{'model_code':model['code'],'days':'0'} for model in data['masters']['models']]
                data['business']={'demands':[{'week':(date(2026,10,12)+timedelta(weeks=week)).isoformat(),'model_code':model['code'],'quantity':100} for week in range(15) for model in data['masters']['models']]}
            if os.getenv('AUTO_PLAN_BROWSER_LINE_STATES_FIXTURE') == '1':
                data['masters']['lines'].append({'code':'B','name':'二线','active':True})
                data['parameters']['capacities'] += [row|{'line_code':'B'} for row in list(data['parameters']['capacities'])]
            if os.getenv('AUTO_PLAN_BROWSER_DEMAND_FIXTURE') == '1':
                for category in ('line_yields','model_yields','capacities'):
                    initial_rows = list(data['parameters'][category])
                    data['parameters'][category] += [row | {'month':month} for month in ('2026-11','2026-12','2027-01') for row in initial_rows]
                data['parameters']['cycle_times'] = [row for row in data['parameters']['cycle_times'] if row['model_code'] != 'P']
                data['parameters']['line_yields'] = [row for row in data['parameters']['line_yields'] if row['model_code'] != 'P']
                data['business'] = {'demands': [
                    {'model_code':'N','week':'2026-10-12','quantity':100},
                    {'model_code':'M','week':'2026-10-19','quantity':1000},
                    {'model_code':'M','week':'2026-10-26','quantity':300},
                    {'model_code':'P','week':'2026-10-26','quantity':500}],
                    'confirmations': [
                        {'model_code':'M','week':'2026-10-19','kind':'material','batch_id':'SOME','status':'allowed','date':'2026-10-15','quantity':400},
                        {'model_code':'N','week':'2026-10-12','kind':'shipping','batch_id':'LATE-OK','status':'allowed','date':'2026-10-30','quantity':100}]}
            if os.getenv('AUTO_PLAN_BROWSER_MATRIX_FIXTURE') == '1':
                data['masters']['lines'] += [{'code':code,'name':name,'active':True} for code,name in [('B','空白线'),('C','零产能线'),('D','缺产能线'),('E','禁排线'),('F','续做线'),('G','未知试排线')]]
                data['masters']['capabilities']=[{'recipe_code':'R','line_code':'E','allowed':False}]
                data['parameters']['capacities'] += [row|{'line_code':code,'pieces':0 if code=='C' and row['recipe_code']=='R' else row['pieces']} for code in ('B','C','D','E','F','G') for row in list(data['parameters']['capacities']) if not (code=='D' and row['recipe_code']=='R')]
                data['calendar']={'holidays':[{'date':'2026-10-15','holiday':True}],
                    'opening_exceptions':[{'date':'2026-10-15','line_code':'L','enabled':True}],
                    'maintenance':[{'date':'2026-10-16','line_code':'L','hours':5}],
                    'experiments':[{'date':'2026-10-16','line_code':'L','hours':3}]}
            if os.getenv('AUTO_PLAN_BROWSER_UNFINISHED_SWITCH_FIXTURE') == '1':
                data['parameters']['switch_durations'][0]['hours'] = '12'
                data['masters']['lines'].append({'code':'B','name':'周三观测线','active':True})
                data['parameters']['capacities'] += [row|{'line_code':'B'} for row in list(data['parameters']['capacities']) if row['line_code']=='L']
            if os.getenv('AUTO_PLAN_BROWSER_DEMAND_TRIAL_FIXTURE') == '1':
                data['parameters']['capacities'] = [row|{'pieces':600} for row in data['parameters']['capacities']]
                data['masters']['lines'].append({'code':'B','name':'零产能线','active':True})
                data['parameters']['capacities'] += [row|{'line_code':'B','pieces':0} for row in list(data['parameters']['capacities']) if row['line_code']=='L']
                data['business'] = {'demands':[{'model_code':model,'week':'2026-10-12','quantity':1000} for model in ('M','N')]}
                if os.getenv('AUTO_PLAN_BROWSER_DEMAND_LATE_INCOMPLETE_FIXTURE') == '1':
                    data['parameters']['cycle_times'] = [row for row in data['parameters']['cycle_times'] if row['model_code'] != 'N']
            if os.getenv('AUTO_PLAN_BROWSER_DEMAND_CONTINUATION_FIXTURE') == '1':
                data['masters']['lines'].append({'code':'B','name':'继续补排线','active':True})
                data['parameters']['capacities'] = [row|{'pieces':600} for row in data['parameters']['capacities']]
                data['parameters']['capacities'] += [row|{'line_code':'B','pieces':1000} for row in list(data['parameters']['capacities'])]
                for category in ('capacities','line_yields','model_yields'):
                    data['parameters'][category] += [row|{'month':'2026-11'} for row in list(data['parameters'][category])]
                for row in data['parameters']['line_yields']:
                    if row['model_code']=='M' and row['month']=='2026-11':
                        row['percent']='80'
                data['business'] = {'demands':[{'model_code':'M','week':'2026-11-02','quantity':1000}]}
            if os.getenv('AUTO_PLAN_BROWSER_DEMAND_COMPARISON_FIXTURE') == '1':
                data['masters']['lines'] += [{'code':code,'name':name,'active':True} for code,name in
                    [('B','高产能线'),('C','已占满线'),('D','零产能线'),('E','缺产能线'),('F','禁排线')]]
                data['masters']['capabilities']=[{'recipe_code':'R','line_code':'F','allowed':False}]
                data['parameters']['capacities']=[row|{'line_code':code,'pieces':0 if code=='D' else 1000 if code in ('B','F') else 600}
                    for code in ('L','B','C','D','E','F') for row in list(data['parameters']['capacities'])
                    if row['line_code']=='L' and not(code=='E' and row['recipe_code']=='R')]
                data['business']={'demands':[{'model_code':model,'week':'2026-10-12','quantity':1000} for model in ('M','N')]}
            inputs.versions[1] = InputSnapshot.model_validate(data)
            inputs.version = 1
            plans = MemoryPlanStore(inputs)
            plan = snapshot()['plan']
            for row in plan['segments']: row['date']='2026-10-15'
            if os.getenv('AUTO_PLAN_BROWSER_WORKSPACE_FIXTURE') == '1':
                models=[model['code'] for model in data['masters']['models'] if model['recipe_code']=='R']
                plan={'initial_states':[{'line_code':line['code'],'recipe_code':'R'} for line in data['masters']['lines']],
                      'segments':[{'id':f'workspace-{week}-{n}','date':(date(2026,10,12)+timedelta(weeks=week,days=3 if week==0 else 0)).isoformat(),'line_code':line['code'],'model_code':models[(week*18+n)%len(models)],'quantity':20,'hours':'1','order':0} for week in range(15) for n,line in enumerate(data['masters']['lines'])]}
            if os.getenv('AUTO_PLAN_BROWSER_DEMAND_FIXTURE') == '1':
                plan = {'initial_states':[{'line_code':'L','recipe_code':'R'}], 'segments':[
                    {'id':'pending-demand','date':'2026-10-15','line_code':'L','order':0,'model_code':'M','quantity':1000,'hours':'24'},
                    {'id':'confirmed-late','date':'2026-10-20','line_code':'L','order':0,'model_code':'N','quantity':100,'hours':'3'}]}
            if os.getenv('AUTO_PLAN_BROWSER_UNFINISHED_SWITCH_FIXTURE') == '1':
                plan = {'segments': []}
            if os.getenv('AUTO_PLAN_BROWSER_DEMAND_TRIAL_FIXTURE') == '1' or os.getenv('AUTO_PLAN_BROWSER_DEMAND_CONTINUATION_FIXTURE') == '1':
                plan = {'segments':[]}
                if os.getenv('AUTO_PLAN_BROWSER_DEMAND_LATE_INCOMPLETE_FIXTURE') == '1':
                    plan = {'segments':[{'id':'incomplete-existing','date':'2026-10-15','line_code':'L','model_code':'N','quantity':100,'hours':'3','order':0}]}
            if os.getenv('AUTO_PLAN_BROWSER_DEMAND_COMPARISON_FIXTURE') == '1':
                plan = {'segments':[{'id':'comparison-full','date':'2026-10-15','line_code':'C','order':0,
                                     'model_code':'N','quantity':600,'hours':'24'}]}
            initial_operations=[]
            if os.getenv('AUTO_PLAN_BROWSER_MATRIX_FIXTURE') == '1':
                plan={'initial_states':[{'line_code':'L','recipe_code':'R'},{'line_code':'F','recipe_code':'R'}],
                    'segments':[{'id':'matrix-p','date':'2026-10-18','line_code':'L','model_code':'P','quantity':100,'hours':'3','order':1},
                                {'id':'matrix-m','date':'2026-10-16','line_code':'F','model_code':'M','quantity':100,'hours':'3','order':0},
                                {'id':'matrix-unknown','date':'2026-10-17','line_code':'G','model_code':'M','quantity':100,'hours':'3','order':0}],
                    'switches':[{'id':'matrix-switch','line_code':'L','from_recipe':'R','to_recipe':'S','allocations':[{'date':'2026-10-18','order':0,'hours':'0.95'}]}]}
                initial_operations=[{'kind':'lock_cells','cells':[{'date':'2026-10-16','line_code':'B','model_code':'N','quantity':0}]}]
            if os.getenv('AUTO_PLAN_BROWSER_LINE_STATES_FIXTURE') != '1' and os.getenv('AUTO_PLAN_BROWSER_MATRIX_FIXTURE') != '1' and os.getenv('AUTO_PLAN_BROWSER_UNFINISHED_SWITCH_FIXTURE') != '1':
                from tests.line_state_support import confirm_states
                initial_operations.append(confirm_states(*[(row['code'],'R') for row in data['masters']['lines']]))
            now = datetime(2026,10,14,tzinfo=timezone.utc)
            PlanService(plans).submit(SubmitPlan(base_revision=0,input_version=1,latest_adoption=0,
                operations=[{'kind':'replace_plan','plan':plan},*initial_operations],operation_id=uuid4(),pending=True),'SHARED_EDITOR',now)
            options['plan_store'] = plans
            options['clock'] = lambda: now
            access.users['SHARED_EDITOR']={'user_id':'SHARED_EDITOR','roles':['editor'],'active':True}
            access.users['SECOND_EDITOR']={'user_id':'SECOND_EDITOR','roles':['editor'],'active':True}
            access.users['SHARED_VIEWER']={'user_id':'SHARED_VIEWER','roles':['viewer'],'active':True}
        if os.getenv('AUTO_PLAN_BROWSER_INITIALIZATION_FIXTURE') == '1':
            from datetime import datetime, timezone
            from tests.plan_support import MemoryPlanStore
            from tests.demand_support import MemoryDemandStore
            plans=MemoryPlanStore(inputs)
            options['plan_store']=plans
            options['demand_store']=MemoryDemandStore(plans)
            options['clock']=lambda: datetime(2026,10,14,tzinfo=timezone.utc)
            access.users['IMPORT_EDITOR']={'user_id':'IMPORT_EDITOR','roles':['editor'],'active':True}
        if os.getenv('AUTO_PLAN_BROWSER_RESET_FIXTURE') == '1':
            from datetime import date, datetime, timezone
            from uuid import uuid4
            from auto_plan.input_models import InputSnapshot
            from auto_plan.plan_models import ExecutionCoverage, ExecutionContext
            from auto_plan.plans import PlanService, SubmitPlan, PlanningContext
            from tests.plan_support import MemoryPlanStore
            from tests.test_plan_preview import snapshot
            inputs.versions[1]=InputSnapshot.model_validate(snapshot()['inputs']|{'input_version':1});inputs.version=1
            plans=MemoryPlanStore(inputs)
            coverage=ExecutionCoverage(start_date='2026-10-12',end_date='2026-10-18',line_codes=['L'],model_codes=['M','N','P'],blanks_are_zero=True)
            now=datetime(2026,10,14,tzinfo=timezone.utc)
            plans.context=PlanningContext(coverage=[coverage],execution=ExecutionContext(coverage=[coverage.model_copy(update={'end_date':date(2026,10,14)})],cutoff=now))
            from tests.line_state_support import confirm_states
            rows=[{'id':identity,'date':day,'line_code':'L','model_code':'M','order':0,'quantity':1000,'hours':'24'} for identity,day in [('early','2026-10-15'),('late','2026-10-16')]]
            PlanService(plans).submit(SubmitPlan(base_revision=0,input_version=1,latest_adoption=0,operation_id=uuid4(),operations=[{'kind':'replace_plan','plan':{'segments':rows}},confirm_states()]),'RESET_EDITOR',now,'adopt')
            options['plan_store']=plans;options['clock']=lambda:now
            access.users['RESET_EDITOR']={'user_id':'RESET_EDITOR','roles':['editor'],'active':True}
        if 'plan_store' in options:
            from tests.jobs_support import MemoryJobStore
            from auto_plan.job_process import JobController
            jobs=MemoryJobStore(options['plan_store'],access)
            options['job_store']=jobs
            options['job_controller']=JobController(jobs,workers=1,clock=options['clock'])
        app = create_app(settings, store=access, input_store=inputs, authenticator=CompanyAuthentication(), **options)
        uvicorn.run(app, host='127.0.0.1', port=port, ssl_keyfile=str(key), ssl_certfile=str(cert), access_log=False)
