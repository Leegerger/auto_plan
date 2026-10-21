"""First-import Excel previews own their complete accepted source before decoding."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import pytest
from io import BytesIO
from threading import Event
from uuid import uuid4

from openpyxl import load_workbook
from tests.test_initialization import import_client, initial_payload


def initial_workbook(client, payload):
    book=load_workbook(BytesIO(client.get('/auto_plan/api/initialization/template.xlsx').content))
    tables=payload['changes']|{key:payload[key] for key in ('segments','demands','coverage','initial_states') if key in payload}
    tables['reset']=[{**payload['reset'],**row} for row in payload['reset']['models']]
    tables['execution_facts']=payload['execution']['facts']
    tables['execution_coverage']=payload['execution']['coverage']
    tables['settings']=[{'complete_weekly_demands':True,'execution_cutoff':payload['execution']['cutoff']}]
    for kind,rows in tables.items():
        sheet=book[kind];headers=[cell.value for cell in sheet[1]]
        for row in rows:sheet.append(['|'.join(row[key]) if isinstance(row.get(key),list) else row.get(key) for key in headers])
    out=BytesIO();book.save(out);return out.getvalue()


def maintained_inputs(client):
    changes=deepcopy(initial_payload()['changes']);changes.pop('demands',None)
    saved=client.post('/auto_plan/api/inputs/apply',json={'base_version':0,'changes':changes})
    assert saved.status_code==200,saved.text
    assert saved.json()['input_version']==1
    return saved.json()


@pytest.mark.parametrize('boundary',[False,True])
def test_excel_preview_keeps_owned_source_and_server_time_while_inputs_advance(import_client,monkeypatch,boundary):
    import auto_plan.initialization_tables as tables
    client=import_client;original=maintained_inputs(client)
    if boundary:
        client.app.state.clock=lambda:datetime(2026,10,14,23,59,tzinfo=timezone.utc)
        assert client.post('/auto_plan/api/auth/login',json={'username':'EDITOR','password':'company-secret'}).status_code==200
    payload=initial_payload()|{'input_version':1};workbook=initial_workbook(client,payload)
    endpoint='/auto_plan/api/initialization/excel/preview?base_revision=0&input_version=1&latest_adoption=0'
    baseline=client.post(endpoint,content=workbook);assert baseline.status_code==200 and baseline.json()['valid']
    entered,release=Event(),Event();decode=tables.excel_bundle
    def coordinated(content,tokens):
        entered.set();assert release.wait(5)
        return decode(content,tokens)
    now=client.app.state.clock()
    with monkeypatch.context() as patch,ThreadPoolExecutor(max_workers=1) as workers:
        patch.setattr(tables,'excel_bundle',coordinated)
        future=workers.submit(client.post,endpoint,content=workbook)
        try:
            assert entered.wait(5)
            models=deepcopy(original['masters']['models']);models[0]['name']='Genuinely changed while decoding'
            changed=client.post('/auto_plan/api/inputs/apply',json={'base_version':1,'changes':{'models':models}})
            assert changed.status_code==200 and changed.json()['input_version']==2
            client.app.state.clock=lambda:now+(timedelta(minutes=2) if boundary else timedelta(seconds=5))
        finally:release.set()
        response=future.result(timeout=5)
    assert response.status_code==200,response.text
    assert response.json()==baseline.json(),'Prepared inputs, plan, context, evaluation, payload and server time must all retain the accepted source.'
    assert client.get('/auto_plan/api/inputs?version=1').json()==original
    head=client.get('/auto_plan/api/plans/current').json()
    assert head['revision']==head['latest_adoption']==0 and head['input_version']==2
    assert head['plan']['segments']==[]
    assert client.get('/auto_plan/api/demands/files').json()=={'input_version':2,'enablement_date':None,'files':[]}
    assert client.post(endpoint,content=workbook).status_code==409
    rejected=client.post('/auto_plan/api/initialization/apply',json=baseline.json()['payload']|{'operation_id':str(uuid4())})
    assert rejected.status_code==409,rejected.text
    assert client.get('/auto_plan/api/plans/current').json()==head
    response.json()['inputs']['masters']['models'][0]['name']='caller mutation'
    fresh=client.post(endpoint.replace('input_version=1','input_version=2'),content=workbook)
    assert fresh.status_code==200 and fresh.json()['input_version']==2
    # After the production-day boundary the same workbook can require updated
    # execution confirmations. Its new-time result must match the existing
    # JSON preview, rather than reusing the prior day's valid result.
    same_time=client.post('/auto_plan/api/initialization/preview',json=baseline.json()['payload']|{'input_version':2})
    assert same_time.status_code==200
    assert {key:value for key,value in fresh.json().items() if key!='payload'}==same_time.json()
    if not boundary: assert fresh.json()['valid']


@pytest.mark.parametrize('query',[{'base_revision':1},{'latest_adoption':1},{'input_version':0}])
def test_excel_stale_admission_leaves_the_complete_live_source_unchanged(import_client,query):
    client=import_client;maintained_inputs(client)
    workbook=initial_workbook(client,initial_payload()|{'input_version':1})
    before=client.get('/auto_plan/api/plans/current').json()
    values={'base_revision':0,'input_version':1,'latest_adoption':0}|query
    from urllib.parse import urlencode
    response=client.post('/auto_plan/api/initialization/excel/preview?'+urlencode(values),content=workbook)
    assert response.status_code==409,response.text
    assert client.get('/auto_plan/api/plans/current').json()==before
    assert client.get('/auto_plan/api/demands/files').json()=={'input_version':1,'enablement_date':None,'files':[]}


def test_clients_cannot_supply_an_initial_source(import_client):
    payload=initial_payload()|{'source':{'input_version':0}}
    assert import_client.post('/auto_plan/api/initialization/preview',json=payload).status_code==422
