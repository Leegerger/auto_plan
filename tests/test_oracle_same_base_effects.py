"""Actual disposable Oracle public closure semantics, separate from encoded units."""
from copy import deepcopy
import json
import os
from uuid import uuid4

import pytest

from auto_plan.oracle_plans import OraclePlanUnit
from auto_plan.plan_models import EvaluationSnapshot
from auto_plan.rules import evaluate
from tests.test_oracle_manual_event_slots import oracle_manual

pytestmark=[pytest.mark.oracle,pytest.mark.skipif(os.getenv('AUTO_PLAN_TEST_ORACLE')!='1',
    reason='Opt-in disposable PLAN_TEST only')]


def canonical(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'))


@pytest.mark.parametrize('context',['preferences','execution'])
def test_real_same_base_preview_save_adopt_context_and_protection(oracle_manual,monkeypatch,context):
    client,plans,proof=oracle_manual
    def current():
        response=client.get('/auto_plan/api/plans/current')
        assert response.status_code==200,response.text
        return response.json()
    def command(*operations):
        head=current()
        return {'base_revision':head['revision'],'input_version':head['input_version'],
            'latest_adoption':head['latest_adoption'],'operations':list(operations),
            'operation_id':str(uuid4()),'pending':True}
    def post(path,payload):
        if path=='preview': payload={key:value for key,value in payload.items() if key not in {'operation_id','pending'}}
        return client.post('/auto_plan/api/plans/'+path,json=payload)
    initial={'segments':[{'id':identity,'date':day,'line_code':'L','model_code':model,
        'quantity':100,'order':0,'hours':'3'} for identity,day,model in
        [('m','2026-10-15','M'),('n','2026-10-16','N')]],
        'initial_states':[{'line_code':'L','recipe_code':'R'}]}
    saved=post('save',command({'kind':'replace_plan','plan':initial}))
    assert saved.status_code==200,saved.text
    adopted=post('adopt',command())
    assert adopted.status_code==200,adopted.text
    source=current()
    old_adoption=client.get('/auto_plan/api/plans/adoptions/1').json()
    history=client.get('/auto_plan/api/plans/history').json()
    revision=client.get('/auto_plan/api/plans/revisions/2').json()
    edit={'kind':'change_quantity','segment_id':'m','quantity':99,'end_date':'2026-10-15',
        'action_id':'same-base-quantity','scope':{'start_date':'2026-10-15','end_date':'2026-10-15',
        'line_codes':['L'],'model_codes':['M']}}
    extra=({'kind':'set_preferences','preferences':{'near_week_weight':'7'}} if context=='preferences' else
        {'kind':'record_execution','rows':[{'date':'2026-10-14','line_code':'L','model_code':'M','quantity':1}],
        'cutoff':'2026-10-14T08:00:00+08:00','action_id':'same-base-observed-execution'})
    request=command(extra,edit)
    response=post('preview',request)
    assert response.status_code==200,response.text
    actual=response.json()
    (proof/'same-base-source.json').write_text(canonical(source))
    (proof/'same-base-request.json').write_text(canonical(request))
    (proof/'same-base-preview.json').write_text(canonical(actual))
    with monkeypatch.context() as fallback:
        fallback.setattr(OraclePlanUnit,'planning_plan_value',lambda self,record:record['snapshot']['plan'])
        fresh=post('preview',request)
        assert fresh.status_code==200,fresh.text
        assert canonical(fresh.json())==canonical(actual)
    assert canonical(actual['evaluation'])==canonical(evaluate(
        EvaluationSnapshot.model_validate(actual['snapshot'])).model_dump(mode='json'))
    assert actual['evaluation']['output_totals']['expected']==(199 if context=='preferences' else 200)
    assert actual['plan']['segments'][0]['quantity']==99
    assert ['CAP','L','2026-10-15'] in actual['effects']
    assert ['CELL','2026-10-15','L','M'] in actual['effects']
    if context=='preferences': assert actual['context']['preferences']['near_week_weight']=='7'
    else:
        assert actual['context']['execution']['facts']==extra['rows']
        assert ['CAP','L','2026-10-14'] in actual['effects']
        assert ['REQ','M','2027-01-18'] in actual['effects']
    assert current()==source
    assert client.get('/auto_plan/api/plans/history').json()==history
    assert client.get('/auto_plan/api/plans/revisions/2').json()==revision
    assert client.get('/auto_plan/api/plans/adoptions/1').json()==old_adoption
    expected=deepcopy(actual)
    actual['effects'].clear()
    actual['snapshot']['inputs']['masters']['models'][0]['name']='caller-owned'
    assert canonical(post('preview',request).json())==canonical(expected)
    committed=post('save',request)
    assert committed.status_code==200,committed.text
    assert post('save',request).json()==committed.json()
    assert post('verify',{'operation_id':request['operation_id']}).json()==committed.json()
    record=client.get('/auto_plan/api/plans/revisions/3').json()
    assert record['effects']==expected['effects'] and record['plan']==expected['plan']
    assert record['context']==expected['context']
    changed=current()
    assert changed['input_version']==source['input_version']+1
    current_snapshot=post('preview',command()).json()['snapshot']
    assert canonical(changed['evaluation'])==canonical(evaluate(
        EvaluationSnapshot.model_validate(current_snapshot)).model_dump(mode='json'))
    adopted=post('adopt',command())
    assert adopted.status_code==200,adopted.text
    assert client.get('/auto_plan/api/plans/adoptions/1').json()==old_adoption
    assert client.get('/auto_plan/api/plans/adoptions/2').json()['snapshot']['plan']==expected['plan']
    lock={'kind':'lock_cells','cells':[{'date':'2026-10-15','line_code':'L','model_code':'M','quantity':99}]}
    assert post('save',command(lock)).status_code==200
    before=current()
    rejected=post('preview',command(edit|{'quantity':98}))
    assert rejected.status_code==409,rejected.text
    assert any(row['code']=='locked_cell' for row in rejected.json()['conflicts'])
    assert current()==before
    assert plans.check_consistency() is True
    (proof/'same-base-source.json').write_text(canonical(source))
    (proof/'same-base-request.json').write_text(canonical(request))
    (proof/'same-base-preview.json').write_text(canonical(expected))
