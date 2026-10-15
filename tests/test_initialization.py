from datetime import datetime, timezone
from uuid import uuid4
from copy import deepcopy
from fastapi.testclient import TestClient
import pytest
from auto_plan.app import create_app
from auto_plan.settings import Settings
from tests.support import MemoryAccessStore, CompanyAuthentication
from tests.input_support import MemoryInputStore
from tests.plan_support import MemoryPlanStore
from tests.demand_support import MemoryDemandStore
from tests.test_plan_preview import snapshot


def initial_payload():
    from datetime import date, timedelta
    source=snapshot()['inputs']
    changes={kind:rows for section in source.values() for kind,rows in section.items()}
    changes['capacities'][0]['pieces']=2000
    return {'base_revision':0,'input_version':0,'latest_adoption':0,'changes':changes,
            'segments':[{'id':'imported','date':'2026-10-15','line_code':'L','model_code':'M','order':7,'quantity':1400}],
            'demands':[{'model_code':'M','week':(date(2026,10,12)+timedelta(weeks=i)).isoformat(),'week_label':(['W202642','W202643','W202644','W202645','W202646','W202647','W202648','W202649','W202650','W202651','W202652','W202653','W202701','W202702','W202703'][i]),'quantity':1000 if i==0 else 0} for i in range(15)],
            'complete_weekly_demands':True,
            'coverage':[{'start_date':'2026-10-12','end_date':'2026-10-25','line_codes':['L'],'model_codes':['M'],'blanks_are_zero':True}],
            'reset':{'batch_id':'external-monday','production_week':'2026-10-12','cutoff':'2026-10-12T08:00:00+08:00','models':[{'model_code':'M','wip':0,'net_balance':600}]},
            'execution':{'facts':[],'coverage':[{'start_date':'2026-10-12','end_date':'2026-10-14','line_codes':['L'],'model_codes':['M'],'blanks_are_zero':True}],'cutoff':'2026-10-14T08:00:00+08:00'}}

@pytest.fixture
def import_client():
    inputs=MemoryInputStore(); plans=MemoryPlanStore(inputs); access=MemoryAccessStore()
    access.users['EDITOR']={'user_id':'EDITOR','roles':['editor'],'active':True}
    app=create_app(Settings(public_origin='https://testserver'),store=access,input_store=inputs,plan_store=plans,demand_store=MemoryDemandStore(plans),authenticator=CompanyAuthentication(),clock=lambda:datetime(2026,10,14,tzinfo=timezone.utc))
    with TestClient(app,base_url='https://testserver') as client:
        client.headers.update({'Origin':'https://testserver','X-Auto-Plan-Request':'1'})
        assert client.post('/auto_plan/api/auth/login',json={'username':'EDITOR','password':'company-secret'}).status_code==200
        yield client


def test_first_import_preview_builds_exact_hours_without_writing_or_deducting_external_balance(import_client):
    client=import_client
    response=client.post('/auto_plan/api/initialization/preview',json=initial_payload())
    assert response.status_code==200,response.text
    result=response.json(); assert result['valid'],result
    assert result['plan']['segments'][0]['quantity']==1400
    assert result['plan']['segments'][0]['hours']
    assert result['lineage']['external_reset']['production_week']=='2026-10-12'
    assert result['lineage']['external_reset']['system_correction_available'] is False
    assert result['inputs']['business']['demands'][0]['quantity']==1000
    assert client.get('/auto_plan/api/plans/current').json()['revision']==0
    assert client.get('/auto_plan/api/inputs?version=0').json()['masters']['models']==[]


def test_first_import_rejects_missing_order_incomplete_week_and_unpaired_snapshot_without_writes(import_client):
    for mutate,field in [(lambda p:p['segments'][0].pop('order'),'order'),(lambda p:p['demands'].pop(),'quantity'),(lambda p:p['reset']['models'][0].pop('net_balance'),'models.0.net_balance')]:
        payload=initial_payload();mutate(payload)
        result=import_client.post('/auto_plan/api/initialization/preview',json=payload).json()
        assert result['valid'] is False
        assert any(row['field']==field for row in result['errors']),result
    assert import_client.get('/auto_plan/api/plans/current').json()['revision']==0


def test_initial_import_saves_inputs_context_demand_file_and_pending_plan_without_adoption(import_client):
    payload=initial_payload()|{'operation_id':str(uuid4())}
    saved=import_client.post('/auto_plan/api/initialization/apply',json=payload)
    assert saved.status_code==200,saved.text
    assert saved.json()['latest_adoption']==0
    assert import_client.post('/auto_plan/api/initialization/apply',json=payload).json()==saved.json()
    current=import_client.get('/auto_plan/api/plans/current').json()
    assert current['plan']['segments'][0]['quantity']==1400
    assert current['pending'] is True
    assert current['coverage'][0]['end_date']=='2026-10-25'
    assert current['lineage']['external_reset']['already_applied'] is True
    files=import_client.get('/auto_plan/api/demands/files').json()['files']
    assert files[0]['attribution_date']=='2026-10-14'
    assert files[0]['rows'][0]['delta']==1000
    assert len(files[0]['rows'])==15


def initialize(client):
    response=client.post('/auto_plan/api/initialization/apply',json=initial_payload()|{'operation_id':str(uuid4())})
    assert response.status_code==200,response.text
    return response.json()


@pytest.mark.parametrize('original_quantity,actual,added,expected',[(600,900,300,1200),(0,900,300,1200)])
def test_imported_surplus_and_plan_free_actual_do_not_execute_new_ids(import_client,original_quantity,actual,added,expected):
    from tests.test_plan_transactions import command,post,row
    payload=initial_payload()
    payload['segments']=[payload['segments'][0]|{'date':'2026-10-14','quantity':original_quantity}] if original_quantity else []
    payload['execution']['facts']=[{'date':'2026-10-14','line_code':'L','model_code':'M','quantity':actual}]
    saved=import_client.post('/auto_plan/api/initialization/apply',json=payload|{'operation_id':str(uuid4())})
    assert saved.status_code==200,saved.text
    original=import_client.get('/auto_plan/api/plans/revisions/1').json()
    head=import_client.get('/auto_plan/api/plans/current').json()
    operation={'kind':'patch_plan','segments':[row('new',day='2026-10-14',quantity=added)|{'order':9,'hours':'4'}]}
    changed=post(import_client,'save',command(operation,base=head['revision'],version=head['input_version']))
    assert changed.status_code==200,changed.text
    current=import_client.get('/auto_plan/api/plans/current').json()
    assert next(r for r in current['execution_segments'] if r['segment_id']=='new')=={'segment_id':'new','executed':0,'remaining':300}
    assert current['evaluation']['output_totals']['expected']==expected
    assert import_client.get('/auto_plan/api/plans/revisions/1').json()==original


@pytest.mark.parametrize('managed_field,value',[
    ('audit',[{'action_id':'fabricated','actor':'OTHER','recorded_at':'2026-10-14T08:00:00+08:00','changes':[]}]),
    ('associations',[{'segment_id':'imported','quantity':0}]),
    ('association_cells',[{'date':'2026-10-15','line_code':'L','model_code':'M','quantity':0}]),
    ('day_capacities',[{'date':'2026-10-14','line_code':'L','remaining_hours':'24','current_recipe':'R','confirmed_at':'2026-10-14T08:00:00+08:00','basis_digest':'fabricated'}]),
])
def test_initial_import_rejects_client_managed_execution_metadata(import_client,managed_field,value):
    payload=initial_payload()
    payload['execution'][managed_field]=value
    checked=import_client.post('/auto_plan/api/initialization/preview',json=payload).json()
    assert checked['valid'] is False
    assert any(row['sheet']=='execution' and row['field']==managed_field for row in checked['errors']),checked
    assert import_client.get('/auto_plan/api/plans/current').json()['revision']==0


def test_same_date_replacement_preserves_identical_rows_and_history_without_double_counting(import_client):
    client=import_client; initialize(client)
    rows=[{'model_code':'M','week':'2026-10-12','delta':200}]*2
    payload={'input_version':1,'rows':rows,'operation_id':str(uuid4())}
    assert client.post('/auto_plan/api/demands/preview',json={k:v for k,v in payload.items() if k!='operation_id'}).json()['demands']==[{'model_code':'M','week':'2026-10-12','quantity':400}]
    assert len(client.get('/auto_plan/api/demands/files').json()['files'])==1
    saved=client.post('/auto_plan/api/demands/apply',json=payload)
    assert saved.status_code==200,saved.text
    assert client.post('/auto_plan/api/demands/apply',json=payload).json()==saved.json()
    again=client.post('/auto_plan/api/demands/apply',json=payload|{'operation_id':str(uuid4()),'input_version':2})
    assert again.status_code==200,again.text
    current=client.get('/auto_plan/api/plans/current').json()
    assert current['revision']==1 and current['input_version']==3
    files=client.get('/auto_plan/api/demands/files').json()['files']
    assert len(files)==3 and [f['effective'] for f in files]==[False,False,True]
    assert len({r['row_id'] for r in files[-1]['rows']})==2
    inputs=client.get('/auto_plan/api/inputs?version=3').json()
    assert inputs['business']['demands']==[{'model_code':'M','week':'2026-10-12','quantity':400}]
    assert len(client.get('/auto_plan/api/inputs?version=1').json()['business']['demands'])==15


def test_historical_replacement_replays_later_rows_and_rejects_intermediate_negative_atomically(import_client):
    client=import_client; initialize(client)
    client.app.state.clock=lambda:datetime(2026,10,15,tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login',json={'username':'EDITOR','password':'company-secret'}).status_code==200
    body={'input_version':1,'attribution_date':'2026-10-15','rows':[{'model_code':'M','week':'2026-10-12','delta':-800}],'operation_id':str(uuid4())}
    response=client.post('/auto_plan/api/demands/apply',json=body)
    assert response.status_code==200,response.text
    revise={'input_version':2,'attribution_date':'2026-10-14','rows':[{'model_code':'M','week':'2026-10-12','delta':500}],'operation_id':str(uuid4())}
    failed=client.post('/auto_plan/api/demands/apply',json=revise)
    assert failed.status_code==422,failed.text
    assert '2026-10-15' in failed.json()['errors'][0]['reason']
    assert client.get('/auto_plan/api/plans/current').json()['input_version']==2
    assert len(client.get('/auto_plan/api/demands/files').json()['files'])==2
    ordered=revise|{'attribution_date':'2026-10-15','rows':[{'model_code':'M','week':'2026-10-19','delta':-100},{'model_code':'M','week':'2026-10-19','delta':200}]}
    result=client.post('/auto_plan/api/demands/preview',json={k:v for k,v in ordered.items() if k!='operation_id'}).json()
    assert not result['valid'] and result['errors'][0]['row']==2
    before_enable=revise|{'attribution_date':'2026-10-13','rows':[]}
    assert not client.post('/auto_plan/api/demands/preview',json={k:v for k,v in before_enable.items() if k!='operation_id'}).json()['valid']


def test_standard_workbook_and_paste_preview_share_initial_import_validation(import_client):
    from io import BytesIO
    from openpyxl import load_workbook
    client=import_client
    template=client.get('/auto_plan/api/initialization/template.xlsx')
    assert template.status_code==200
    book=load_workbook(BytesIO(template.content))
    payload=initial_payload()
    for kind,rows in payload['changes'].items():
        for row in rows: book[kind].append([row.get(cell.value) for cell in book[kind][1]])
    for kind in ('segments','demands','coverage'):
        for row in payload[kind]: book[kind].append([('|'.join(row.get(c.value,[])) if c.value in {'line_codes','model_codes'} else row.get(c.value)) for c in book[kind][1]])
    for row in payload['reset']['models']: book['reset'].append([payload['reset'].get(c.value,row.get(c.value)) for c in book['reset'][1]])
    for row in payload['execution']['coverage']: book['execution_coverage'].append([('|'.join(row[c.value]) if c.value in {'line_codes','model_codes'} else row[c.value]) for c in book['execution_coverage'][1]])
    book['settings'].append([True,payload['execution']['cutoff']])
    out=BytesIO();book.save(out)
    response=client.post('/auto_plan/api/initialization/excel/preview?base_revision=0&input_version=0&latest_adoption=0',content=out.getvalue())
    assert response.status_code==200,response.text
    assert response.json()['valid'],response.json()
    assert response.json()['payload']['segments'][0]['order']==7
    texts={sheet.title:'\n'.join('\t'.join('' if c is None else str(c).lower() if type(c) is bool else str(c) for c in row) for row in sheet.values) for sheet in book}
    paste=client.post('/auto_plan/api/initialization/paste/preview',json={'base_revision':0,'input_version':0,'latest_adoption':0,'tables':texts})
    assert paste.status_code==200,paste.text
    assert paste.json()['valid'],paste.json()
    book['segments']['D2']=''
    out=BytesIO();book.save(out)
    invalid=client.post('/auto_plan/api/initialization/excel/preview?base_revision=0&input_version=0&latest_adoption=0',content=out.getvalue())
    assert invalid.json()['valid'] is False
    assert any(e['field']=='order' for e in invalid.json()['errors'])
    assert client.get('/auto_plan/api/plans/current').json()['revision']==0


def test_initialization_rejects_duplicate_facts_and_segments_outside_declared_old_coverage(import_client):
    payload=initial_payload()
    payload['execution']['facts']=[{'date':'2026-10-13','line_code':'L','model_code':'M','quantity':100}]*2
    failed=import_client.post('/auto_plan/api/initialization/preview',json=payload).json()
    assert not failed['valid'] and any(e['field']=='facts' for e in failed['errors'])
    payload=initial_payload();payload['coverage'][0]['end_date']='2026-10-14'
    failed=import_client.post('/auto_plan/api/initialization/preview',json=payload).json()
    assert not failed['valid'] and any(e['field']=='coverage' for e in failed['errors'])


def test_beijing_natural_demand_date_is_independent_of_0800_production_date_and_iso_year_labels(import_client):
    client=import_client
    client.app.state.clock=lambda:datetime(2026,10,13,23,tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login',json={'username':'EDITOR','password':'company-secret'}).status_code==200
    payload=initial_payload()|{'operation_id':str(uuid4())}
    payload['execution']['cutoff']='2026-10-13T08:00:00+08:00'
    assert client.post('/auto_plan/api/initialization/apply',json=payload).status_code==200
    assert client.get('/auto_plan/api/demands/files').json()['files'][0]['attribution_date']=='2026-10-14'
    examples=[('2025-12-29','W202601'),('2026-01-05','W202602'),('2026-12-28','W202653'),('2027-01-04','W202701')]
    payload={'input_version':1,'rows':[{'model_code':'M','week':day,'week_label':label,'delta':100} for day,label in examples]}
    checked=client.post('/auto_plan/api/demands/preview',json=payload)
    assert checked.status_code==200 and checked.json()['valid'],checked.text
    assert checked.json()['attribution_date']=='2026-10-14'
    payload['rows'][0]['week_label']='W202553'
    wrong=client.post('/auto_plan/api/demands/preview',json=payload).json()
    assert not wrong['valid'] and wrong['errors'][0]['field']=='week_label'
    payload=initial_payload();payload['demands'][0]['week_label']='W202643'
    wrong=client.post('/auto_plan/api/initialization/preview',json=payload)
    assert wrong.status_code==409


def test_demand_preview_recalculates_existing_plan_impact_without_rearranging_or_writing(import_client):
    client=import_client;initialize(client)
    response=client.post('/auto_plan/api/demands/preview',json={'input_version':1,'rows':[{'model_code':'M','week':'2026-10-12','delta':2000}]})
    checked=response.json()
    assert checked['valid'] and checked['evaluation']['weekly_results'][0]['gap']>0
    assert checked['plan']['segments'][0]['quantity']==1400
    assert client.get('/auto_plan/api/plans/current').json()['input_version']==1


def test_initial_full_demand_keeps_already_satisfied_part_instead_of_importing_only_gap(import_client):
    payload=initial_payload()
    payload['segments'][0].update(date='2026-10-13',quantity=400)
    payload['execution']['facts']=[{'date':'2026-10-13','line_code':'L','model_code':'M','quantity':400}]
    checked=import_client.post('/auto_plan/api/initialization/preview',json=payload).json()
    assert checked['valid'],checked
    week=next(r for r in checked['evaluation']['weekly_results'] if r['week']=='2026-10-12')
    assert (week['demand'],week['on_time'],week['gap'])==(1000,400,600)
    assert checked['inputs']['business']['demands'][0]['quantity']==1000
