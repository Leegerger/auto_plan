"""Unrun public guards. Encoded Oracle units are not actual Oracle evidence."""
from tests.line_state_support import confirm_states
from copy import deepcopy
import json
from uuid import uuid4

import pytest

from auto_plan.input_models import InputSnapshot
from auto_plan.oracle_plans import OraclePlanUnit
from auto_plan.plan_models import EvaluationSnapshot
from auto_plan.plans import PlanService
from auto_plan.rules import evaluate
from tests.test_owned_plan import EncodedRecordCursor
from tests.test_plan_transactions import shared_client,command,post,row


def canonical(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'))


class CopyingSnapshot(EvaluationSnapshot):
    """A valid conservative adapter whose copies own equal dependency DTOs."""
    def model_copy(self,*,update=None,deep=False):
        values=dict(update or {})
        values.setdefault('inputs',self.inputs.model_copy(deep=True))
        values.setdefault('time',self.time.model_copy(deep=True))
        return super().model_copy(update=values,deep=deep)


@pytest.fixture
def closure_client(shared_client,monkeypatch):
    client,plans,inputs=shared_client
    data=inputs.read().model_dump(mode='json')
    data['business']['demands']=[{'model_code':model,'week':'2026-10-12','quantity':100} for model in ('M','N')]
    inputs.versions[1]=InputSnapshot.model_validate(data)
    initial={'segments':[row('m'),row('n','N','2026-10-16')],
        'initial_states':[{'line_code':'L','recipe_code':'R'}]}
    assert post(client,'save',command({'kind':'replace_plan','plan':initial})).status_code==200
    assert post(client,'adopt',command(confirm_states(), base=1)).status_code==200
    mode=['owned']
    memory_read=plans.read
    snapshot=PlanService._snapshot
    def scoped_read(actor,action):
        if mode[0]=='memory': return memory_read(actor,action)
        unit=OraclePlanUnit(plans,EncodedRecordCursor(plans),(plans.revision,plans.adoption),
            inputs.read().model_copy(deep=True),plans.context.model_copy(deep=True))
        if mode[0]=='raw': unit.planning_plan_value=lambda record:record['snapshot']['plan']
        # The encoded cursor covers immutable records/effects only. Keep the
        # ordinary history implementation for this adapter's public history.
        unit.list_history=plans.list_history
        before=canonical(unit.plan.model_dump(mode='json'))
        result=action(unit)
        assert canonical(unit.plan.model_dump(mode='json'))==before
        return result
    def scoped_snapshot(self,unit,plan,now):
        result=snapshot(self,unit,plan,now)
        return CopyingSnapshot.model_validate(result.model_dump(mode='json')) if mode[0]=='copying' else result
    monkeypatch.setattr(plans,'read',scoped_read)
    monkeypatch.setattr(PlanService,'_snapshot',scoped_snapshot)
    return client,plans,mode


def quantity(identity='m',model='M',day='2026-10-15',pieces=99):
    return {'kind':'change_quantity','segment_id':identity,'quantity':pieces,'end_date':day,
        'action_id':str(uuid4()),'scope':{'start_date':day,'end_date':day,'line_codes':['L'],'model_codes':[model]}}


def compare_modes(client,mode,payload,status=200):
    results=[]
    for selected in ('owned','raw','memory','copying'):
        mode[0]=selected
        response=post(client,'preview',payload)
        assert response.status_code==status,response.text
        results.append(response.json())
    mode[0]='owned'
    assert all(canonical(value)==canonical(results[0]) for value in results)
    if status==200:
        actual=results[0]
        independent=evaluate(EvaluationSnapshot.model_validate(actual['snapshot'])).model_dump(mode='json')
        assert canonical(actual['evaluation'])==canonical(independent)
    return results[0]


@pytest.mark.parametrize('context_change',['none','preferences','lock','execution'])
def test_current_base_preserves_complete_effects_context_evaluation_and_ownership(closure_client,context_change):
    client,plans,mode=closure_client
    source=client.get('/auto_plan/api/plans/current').json()
    adoption=client.get('/auto_plan/api/plans/adoptions/1').json()
    history=client.get('/auto_plan/api/plans/history').json()
    extra={'none':[],
        'preferences':[{'kind':'set_preferences','preferences':{'near_week_weight':'7'}}],
        'lock':[{'kind':'lock_cells','cells':[{'date':'2026-10-17','line_code':'B','model_code':'N','quantity':0}]}],
        'execution':[{'kind':'record_execution','rows':[{'date':'2026-10-14','line_code':'L','model_code':'M','quantity':1}],
            'cutoff':'2026-10-14T08:00:00+08:00','action_id':'declared-execution-context'}]}[context_change]
    payload={'base_revision':source['revision'],'input_version':source['input_version'],
        'latest_adoption':source['latest_adoption'],'operations':extra+[quantity()]}
    actual=compare_modes(client,mode,payload)
    assert actual['merged'] is False
    assert next(r for r in actual['plan']['segments'] if r['id']=='m')['quantity']==99
    if context_change=='none':
        assert actual['evaluation']['output_totals']['expected']==199
        assert actual['evaluation']['output_totals']['on_time']==199
        assert actual['evaluation']['output_totals']['gap']==1
        assert actual['effects']==[['CAP','L','2026-10-15'],['CELL','2026-10-15','B','M'],
            ['CELL','2026-10-15','L','M'],['REQ','M','2026-10-12']]
    elif context_change=='preferences':
        assert actual['context']['preferences']['near_week_weight']=='7'
        assert actual['evaluation']['stability']['weighted_cost']=='7'
    elif context_change=='lock':
        assert ['CELL','2026-10-17','B','N'] in actual['effects']
        assert actual['context']['locks']==extra[0]['cells']
    else:
        assert actual['context']['execution']['facts']==extra[0]['rows']
        assert ['CAP','L','2026-10-14'] in actual['effects']
        assert ['REQ','M','2027-01-18'] in actual['effects']
    expected=canonical(actual)
    actual['effects'].clear()
    actual['plan']['segments'][0]['quantity']=999
    actual['context']['execution']['facts'].clear()
    mode[0]='owned'
    assert canonical(post(client,'preview',payload).json())==expected
    assert client.get('/auto_plan/api/plans/current').json()==source
    assert client.get('/auto_plan/api/plans/adoptions/1').json()==adoption
    assert client.get('/auto_plan/api/plans/history').json()==history


def test_stale_replay_and_conflict_keep_original_failures_and_complete_merged_effects(closure_client):
    client,plans,mode=closure_client
    assert post(client,'save',command(quantity('n','N','2026-10-16',90),base=2,version=2,adoption=1)).status_code==200
    source=client.get('/auto_plan/api/plans/current').json()
    payload={'base_revision':2,'input_version':2,'latest_adoption':1,'operations':[quantity()]}
    merged=compare_modes(client,mode,payload)
    assert merged['merged'] is True and merged['evaluation']['output_totals']['expected']==189
    assert {row['id']:row['quantity'] for row in merged['plan']['segments']}=={'m':99,'n':90}
    conflict=compare_modes(client,mode,payload|{'operations':[quantity('n','N','2026-10-16')]},409)
    assert ['REQ','N','2026-10-12'] in conflict['conflicts']
    compare_modes(client,mode,payload|{'operations':[quantity('missing')]},422)
    assert client.get('/auto_plan/api/plans/current').json()==source
