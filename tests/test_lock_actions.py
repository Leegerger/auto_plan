"""Aggregate locking and preferences use the same public operation queue and guard."""
from tests.line_state_support import confirm_states
from tests.test_plan_transactions import shared_client, post, command, patch, row


def test_batch_lock_including_zero_advances_guard_and_requires_explicit_earlier_unlock(shared_client):
    client,plans,_=shared_client
    first=post(client,'save',command(patch(row(quantity=100))))
    assert first.status_code==200,first.text
    locks=[{'date':'2026-10-15','line_code':'L','model_code':'M','quantity':100},
           {'date':'2026-10-15','line_code':'L','model_code':'N','quantity':0}]
    action={'kind':'lock_cells','cells':locks}
    before=client.get('/auto_plan/api/plans/current').json()
    probe=post(client,'preview',{k:v for k,v in command(action,base=1).items() if k not in ('operation_id','pending')})
    assert probe.status_code==200,probe.text
    assert probe.json()['context']['locks']==locks
    assert client.get('/auto_plan/api/plans/current').json()==before
    payload=command(action,base=1)
    saved=post(client,'save',payload)
    assert saved.status_code==200,saved.text
    assert saved.json()['input_version']==2
    assert post(client,'verify',{'operation_id':payload['operation_id']}).json()==saved.json()
    assert post(client,'save',payload).json()==saved.json()
    stale=post(client,'preview',{k:v for k,v in command(patch(row(quantity=99)),base=2).items() if k not in ('operation_id','pending')})
    assert stale.status_code==409
    assert 'context' in stale.json()
    bypass=post(client,'save',command(patch(row('extra',quantity=1)),base=2,version=2))
    assert bypass.status_code==422 or bypass.status_code==409
    zero=post(client,'save',command(patch(row('new','N',quantity=1)),base=2,version=2))
    assert zero.status_code in (409,422)
    unlock={'kind':'unlock_cells','cells':[{'date':'2026-10-15','line_code':'L','model_code':'M'}]}
    late=post(client,'save',command(patch(row(quantity=90)),unlock,base=2,version=2))
    assert late.status_code in (409,422)
    allowed=post(client,'save',command(unlock,patch(row(quantity=90)),base=2,version=2))
    assert allowed.status_code==200,allowed.text
    assert allowed.json()['input_version']==3
    assert client.get('/auto_plan/api/plans/current').json()['context']['locks']==[locks[1]]


def test_preference_context_and_lock_then_order_share_one_revision_without_losing_lineage(shared_client):
    client,plans,_=shared_client
    plans.context=plans.context.model_copy(update={'lineage':{'external_reset':{'batch_id':'first','already_applied':True}}})
    first=post(client,'save',command(patch(row()),patch(row('n','N')|{'order':1})))
    assert first.status_code==200,first.text
    pref={'kind':'set_preferences','preferences':{'near_weeks':6,'near_tolerance_percent':'15','far_tolerance_percent':'50','near_week_weight':'4','far_week_weight':'1'}}
    lock={'kind':'lock_cells','cells':[{'date':'2026-10-15','line_code':'L','model_code':'M','quantity':100}]}
    # Quantity locks do not lock day order, even for two models of the same Recipe.
    changed=post(client,'save',command(lock,pref,patch(row()|{'order':2}),base=1))
    assert changed.status_code==200,changed.text
    head=client.get('/auto_plan/api/plans/current').json()
    assert (head['revision'],head['input_version'],head['context']['preferences']['near_weeks'])==(2,2,6)
    assert head['context']['lineage']['external_reset']['already_applied'] is True
    assert len(head['context']['locks'])==1
    assert head['plan']['segments'][0]['order']==2
    old=client.get('/auto_plan/api/plans/revisions/1').json()
    assert old['snapshot']['preferences']['near_weeks']==4
    assert old['evaluation']['stability']['baseline_version'] is None


def test_explicit_adoption_establishes_named_blank_zero_cells_in_a_new_positive_week(shared_client):
    client,plans,_=shared_client
    from auto_plan.plan_models import ExecutionCoverage
    plans.context=plans.context.model_copy(update={'coverage':[ExecutionCoverage(start_date='2026-10-12',end_date='2026-10-18',line_codes=['L'],model_codes=['M','N','P'],blanks_are_zero=True)]})
    assert post(client,'adopt',command(confirm_states(), patch(row()))).status_code==200
    original=client.get('/auto_plan/api/plans/adoptions/1').json()
    imported=[{'start_date':'2026-10-12','end_date':'2026-10-18','line_codes':['L'],'model_codes':['M','N','P'],'blanks_are_zero':True}]
    assert original['coverage']==imported
    tail=row('tail',day='2026-10-20')
    payload=command(patch(tail),base=1,adoption=1, version=2)
    preview=post(client,'preview',{k:v for k,v in payload.items() if k not in ('operation_id','pending')})
    assert preview.json()['evaluation']['stability']['weeks'][1]['state']=='new_plan'
    saved=post(client,'save',payload)
    assert saved.status_code==200,saved.text
    shared=client.get('/auto_plan/api/plans/current').json()
    assert shared['latest_adoption']==1
    assert shared['evaluation']['stability']['weeks'][1]['state']=='new_plan'
    adopted=post(client,'adopt',command(base=2,adoption=1, version=2))
    assert adopted.status_code==200,adopted.text
    baseline=client.get('/auto_plan/api/plans/adoptions/2').json()
    assert baseline['coverage']==imported+[{'start_date':'2026-10-19','end_date':'2026-10-25',
        'line_codes':['B','L'],'model_codes':['M','N','P'],'blanks_are_zero':True}]
    source=client.get('/auto_plan/api/plans/current').json()
    assert source['context']['coverage']==imported
    changed=command(patch(tail|{'quantity':90}),patch(row('blank','N',day='2026-10-21',quantity=20)),base=3,adoption=2, version=2)
    probe=post(client,'preview',{k:v for k,v in changed.items() if k not in ('operation_id','pending')})
    assert probe.status_code==200,probe.text
    stability=probe.json()['evaluation']['stability']
    week=stability['weeks'][1]
    assert (week['old_remaining'],week['new_remaining'],week['absolute_change'],week['rate'],week['unknown_baseline_cells'])==(100,110,30,'3/10',0)
    assert (week['state'],week['baseline_complete'])==('compared',True)
    assert stability['weighted_cost']=='90'
    assert stability['weeks'][2]['state']=='new_plan'
    assert client.get('/auto_plan/api/plans/adoptions/2').json()==baseline
    assert client.get('/auto_plan/api/plans/adoptions/1').json()==original
    assert original['evaluation']['stability']['baseline_version'] is None
    assert client.get('/auto_plan/api/plans/current').json()==source


def test_soft_tolerance_warning_does_not_block_adoption(shared_client):
    client,plans,_=shared_client
    from auto_plan.plan_models import ExecutionCoverage
    plans.context=plans.context.model_copy(update={'coverage':[ExecutionCoverage(start_date='2026-10-12',end_date='2026-10-18',line_codes=['L'],model_codes=['M','N','P'],blanks_are_zero=True)]})
    assert post(client,'adopt',command(confirm_states(), patch(row()))).status_code==200
    payload=command(patch(row(quantity=200)|{'hours':'6'}),base=1,adoption=1, version=2)
    preview=post(client,'preview',{k:v for k,v in payload.items() if k not in ('operation_id','pending')})
    assert preview.json()['evaluation']['stability']['weeks'][0]['exceeds_tolerance'] is True
    assert preview.json()['evaluation']['executable'] is True
    assert post(client,'adopt',payload).status_code==200


def test_existing_historical_lock_can_be_explicitly_unlocked_after_window_roll(shared_client):
    client,plans,_=shared_client
    from auto_plan.plan_models import PlanCell
    plans.context=plans.context.model_copy(update={'locks':[PlanCell(date='2026-10-05',line_code='L',model_code='M',quantity=100)]})
    payload=command({'kind':'unlock_cells','cells':[{'date':'2026-10-05','line_code':'L','model_code':'M'}]})
    result=post(client,'save',payload)
    assert result.status_code==200,result.text
    head=client.get('/auto_plan/api/plans/current').json()
    assert head['context']['locks']==[] and head['input_version']==2
