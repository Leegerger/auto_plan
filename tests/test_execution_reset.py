"""Facts and original-source weekly resets through the agreed business API."""
from copy import deepcopy
from datetime import date
import pytest
from tests.test_plan_transactions import shared_client, post, command, patch, row


def head(client):
    response=client.get('/auto_plan/api/plans/current')
    assert response.status_code==200,response.text
    return response.json()


def current_command(client,*operations):
    current=head(client)
    return command(*operations,base=current['revision'],version=current['input_version'],adoption=current['latest_adoption'])


def preview(client,*operations):
    return post(client,'preview',{k:v for k,v in current_command(client,*operations).items() if k not in ('operation_id','pending')})


def fact(quantity,model='M',day='2026-10-14',line='L'):
    return {'date':day,'line_code':line,'model_code':model,'quantity':quantity}


def test_adopting_a_new_planned_week_establishes_blank_zero_cells_and_survives_later_clearing(shared_client):
    client,plans,_=shared_client
    from auto_plan.plan_models import ExecutionCoverage
    original={'start_date':'2026-10-15','end_date':'2026-10-15','line_codes':['L'],'model_codes':['M'],'blanks_are_zero':True}
    plans.context=plans.context.model_copy(update={'coverage':[ExecutionCoverage.model_validate(original)]})
    operation=patch(row('new',day='2026-10-20'))
    assert post(client,'save',current_command(client,operation)).status_code==200
    assert head(client)['coverage']==[original]
    assert post(client,'adopt',current_command(client)).status_code==200
    adopted=client.get('/auto_plan/api/plans/adoptions/1').json()
    assert original in adopted['coverage']
    added=patch(row('blank','N','2026-10-21','B'))
    checked=preview(client,added).json()['evaluation']['stability']
    assert checked['weeks'][1]['baseline_complete'] is True
    assert checked['weighted_cost']=='300'
    assert checked['weeks'][2]['state']=='new_plan'
    assert post(client,'adopt',current_command(client,{'kind':'patch_plan','remove_segments':['new']})).status_code==200
    after=preview(client,added).json()['evaluation']['stability']['weeks'][1]
    assert (after['state'],after['baseline_complete'],after['added'])==('zero_baseline',True,100)


def test_rollover_retains_known_recipe_through_unchanged_expired_work_but_never_infers_an_unknown_state(shared_client):
    client,plans,_=shared_client
    from datetime import datetime, timezone
    from auto_plan.plan_models import ExecutionCoverage
    plans.context=plans.context.model_copy(update={'coverage':[ExecutionCoverage(start_date='2026-10-12',end_date='2026-10-25',line_codes=['L','B'],model_codes=['M','N','P'],blanks_are_zero=True)]})
    source={'initial_states':[{'line_code':'L','recipe_code':'R'},{'line_code':'B','recipe_code':None}],
        'segments':[row('past-known',day='2026-10-15'),row('past-unknown',day='2026-10-15',line='B'),
                    row('future-known',day='2026-10-20'),row('future-unknown',day='2026-10-20',line='B')]}
    assert post(client,'adopt',current_command(client,{'kind':'replace_plan','plan':source})).status_code==200
    client.app.state.clock=lambda:datetime(2026,10,19,tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login',json={'username':'EDITOR','password':'company-secret'}).status_code==200
    execution={'kind':'record_execution','rows':[],'coverage':[{'start_date':'2026-10-19','end_date':'2026-10-19','line_codes':['L','B'],'model_codes':['M','N','P'],'blanks_are_zero':True}],'cutoff':'2026-10-19T08:00:00+08:00'}
    snapshot=reset_snapshot(0)|{'production_week':'2026-10-19','cutoff':'2026-10-19T08:00:00+08:00'}
    checked=preview(client,execution,reset_operation(snapshot=snapshot,scope={'start_date':'2026-10-19','end_date':'2027-01-31','line_codes':['L','B'],'model_codes':[]}))
    assert checked.status_code==200,checked.text
    states={r['line_code']:r['recipe_code'] for r in checked.json()['plan']['initial_states']}
    assert states=={'L':'R','B':None}
    assert all(r['date']>='2026-10-19' for r in checked.json()['plan']['segments'])


def test_reset_participants_exclude_retired_unused_models_and_include_source_and_fact_models(shared_client):
    client,_,_=shared_client
    assert post(client,'adopt',current_command(client,patch(row()))).status_code==200
    inputs=client.get('/auto_plan/api/inputs').json()
    models=[r|{'active':False} for r in inputs['masters']['models']]
    models.append({'code':'RETIRED','name':'Unused retired model','recipe_code':'R','active':False})
    applied=client.post('/auto_plan/api/inputs/apply',json={'base_version':inputs['input_version'],'changes':{'models':models}})
    assert applied.status_code==200,applied.text
    execution={'kind':'record_execution','rows':[fact(0,'N')],'cutoff':'2026-10-14T08:00:00+08:00'}
    checked=preview(client,execution)
    assert checked.status_code==200,checked.text
    assert checked.json()['reset_participants']['current']=={'source_adoption':1,'model_codes':['M','N']}
    assert post(client,'save',current_command(client,execution)).status_code==200
    assert head(client)['reset_participants']['current']=={'source_adoption':1,'model_codes':['M','N']}
    assert head(client)['reset_participants']['correction'] is None


@pytest.mark.parametrize('later_unresolved',[False,True])
@pytest.mark.parametrize('actual_day',['2026-10-16','2026-10-17'])
def test_rollover_keeps_unambiguous_actual_last_recipe_after_a_historical_switch(shared_client,later_unresolved,actual_day):
    client,plans,_=shared_client
    from datetime import datetime, timezone
    from auto_plan.plan_models import ExecutionCoverage
    plans.context=plans.context.model_copy(update={'coverage':[ExecutionCoverage(start_date='2026-10-12',end_date='2026-10-25',line_codes=['L','B'],model_codes=['M','N','P'],blanks_are_zero=True)]})
    source={'initial_states':[{'line_code':'L','recipe_code':'R'}],
        'segments':[row('old-r',day='2026-10-15'),row('actual-s','P',actual_day)|{'order':1 if actual_day=='2026-10-16' else 0},row('future-s','P','2026-10-20')],
        'switches':[{'id':'historical-r-s','line_code':'L','from_recipe':'R','to_recipe':'S','allocations':[{'date':'2026-10-16','order':0,'hours':'0.95'}]}]}
    if later_unresolved:
        source['segments'].append(row('unresolved-r',day='2026-10-18')|{'order':1})
        source['switches'] += [
            {'id':'unconfirmed-s-r','line_code':'L','from_recipe':'S','to_recipe':'R','allocations':[{'date':'2026-10-18','order':0,'hours':'2'}]},
            {'id':'next-r-s','line_code':'L','from_recipe':'R','to_recipe':'S','allocations':[{'date':'2026-10-19','order':0,'hours':'0.95'}]}]
    assert post(client,'adopt',current_command(client,{'kind':'replace_plan','plan':source})).status_code==200
    client.app.state.clock=lambda:datetime(2026,10,19,tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login',json={'username':'EDITOR','password':'company-secret'}).status_code==200
    execution={'kind':'record_execution','rows':[fact(100,'P',actual_day)],
        'coverage':[{'start_date':'2026-10-19','end_date':'2026-10-19','line_codes':['L','B'],'model_codes':['M','N','P'],'blanks_are_zero':True}],'cutoff':'2026-10-19T08:00:00+08:00'}
    snapshot=reset_snapshot(0)|{'production_week':'2026-10-19','cutoff':'2026-10-19T08:00:00+08:00'}
    checked=preview(client,execution,reset_operation(snapshot=snapshot,scope={'start_date':'2026-10-19','end_date':'2027-01-31','line_codes':['L','B'],'model_codes':[]}))
    assert checked.status_code==200,checked.text
    assert checked.json()['plan']['initial_states']==[{'line_code':'L','recipe_code':None if later_unresolved else 'S'}]
    assert checked.json()['evaluation']['capacity_valid'] is (not later_unresolved)


def test_cumulative_facts_replace_omissions_survive_and_segment_associations_are_audited(shared_client):
    client,_,_=shared_client
    rows=[row('one',day='2026-10-14',quantity=500)|{'hours':'12'},row('two',day='2026-10-14',quantity=500)|{'order':1,'hours':'12'}]
    assert post(client,'save',current_command(client,{'kind':'patch_plan','segments':rows})).status_code==200
    action={'kind':'record_execution','rows':[fact(600),fact(100,'N')],'cutoff':'2026-10-14T08:00:00+08:00'}
    before=head(client)
    checked=preview(client,action)
    assert checked.status_code==200,checked.text
    assert head(client)==before
    saved=post(client,'save',current_command(client,action))
    assert saved.status_code==200,saved.text
    current=head(client)
    assert current['context']['execution']['facts']==[fact(600),fact(100,'N')]
    assert current['execution_segments']==[{'segment_id':'one','executed':500,'remaining':0},{'segment_id':'two','executed':100,'remaining':400}]
    assert current['input_version']==2
    assert post(client,'save',current_command(client,action)).status_code==200
    assert head(client)['context']['execution']==current['context']['execution']
    assert head(client)['input_version']==2
    correction={'kind':'record_execution','rows':[fact(550)]}
    rejected=preview(client,correction)
    assert rejected.status_code==422 and '原因' in rejected.json()['detail']
    corrected=post(client,'save',current_command(client,correction|{'reason':'清点更正'}))
    assert corrected.status_code==200,corrected.text
    current=head(client)
    assert current['context']['execution']['facts']==[fact(550),fact(100,'N')]
    assert current['execution_segments'][1]=={'segment_id':'two','executed':50,'remaining':450}
    assert current['context']['execution']['audit'][-1]['reason']=='清点更正'
    association={'kind':'record_execution','rows':[],'associations':[{'segment_id':'one','quantity':50},{'segment_id':'two','quantity':500}],'reason':'按报工分段复核'}
    corrected=post(client,'save',current_command(client,association))
    assert corrected.status_code==200,corrected.text
    assert head(client)['execution_segments']==[{'segment_id':'one','executed':50,'remaining':450},{'segment_id':'two','executed':500,'remaining':0}]
    assert head(client)['context']['execution']['facts'][0]['quantity']==550
    historical=client.get('/auto_plan/api/plans/revisions/2').json()
    assert historical['snapshot']['execution']['facts'][0]['quantity']==600


def test_past_input_additions_are_prohibited_across_patch_replace_move_and_copy(shared_client):
    client,_,_=shared_client
    assert post(client,'save',current_command(client,patch(row()))).status_code==200
    past=row('past',day='2026-10-13')
    scope={'start_date':'2026-10-12','end_date':'2026-10-18','line_codes':['L'],'model_codes':[]}
    commands=[patch(past),{'kind':'replace_plan','plan':{'segments':[past]}},
        {'kind':'move_segment','segment_id':'a','target_date':'2026-10-13','target_line':'L','target_order':0,'scope':scope},
        {'kind':'copy_segments','segment_ids':['a'],'target_start_date':'2026-10-13','line_mapping':{'L':'L'},'scope':scope}]
    for operation in commands:
        response=preview(client,operation)
        assert response.status_code in (409,422),response.text
        assert any(item['code']=='past_input' for item in response.json().get('conflicts',[])),response.text
    assert head(client)['revision']==1


def test_today_automatic_fill_requires_current_recipe_and_remaining_hours_without_double_execution(shared_client):
    client,_,inputs=shared_client
    from auto_plan.input_models import InputSnapshot
    data=inputs.read().model_dump(mode='json')
    for capacity in data['parameters']['capacities']: capacity['pieces']=4800
    inputs.versions[1]=InputSnapshot.model_validate(data)
    scope={'start_date':'2026-10-14','end_date':'2026-10-14','line_codes':['L'],'model_codes':[]}
    fill={'kind':'append_fill','quantity':200,'model_code':'M','line_code':'L','start_date':'2026-10-14','end_date':'2026-10-14','scope':scope}
    checked=preview(client,fill)
    assert checked.status_code==200,checked.text
    assert checked.json()['plan']['segments']==[]
    assert checked.json()['unallocated'][0]['quantity']==200
    # 600 already executed is historical consumption; the confirmed hour is residual.
    assert post(client,'save',current_command(client,patch(row(day='2026-10-14',quantity=600)|{'hours':'3'}),
        {'kind':'record_execution','rows':[fact(600)],'cutoff':'2026-10-14T08:00:00+08:00'})).status_code==200
    confirmation={'kind':'confirm_day_capacity','confirmations':[{'line_code':'L','remaining_hours':'1','current_recipe':'R'}],'observed_at':'2026-10-14T08:00:00+08:00'}
    checked=preview(client,confirmation,fill)
    assert checked.status_code==200,checked.text
    assert sum(r['quantity'] for r in checked.json()['plan']['segments'])==800
    assert checked.json()['evaluation']['executable'] is True
    assert post(client,'save',current_command(client,confirmation)).status_code==200
    assert sum(r['quantity'] for r in preview(client,fill).json()['plan']['segments'])==800
    assert post(client,'save',current_command(client,{'kind':'record_execution','rows':[fact(601)],'reason':'再次清点'})).status_code==200
    assert preview(client,fill).json()['unallocated'][0]['quantity']==200


def reset_snapshot(balance=600,batch='monday',wip=0):
    return {'batch_id':batch,'production_week':'2026-10-12','cutoff':'2026-10-12T08:00:00+08:00',
        'models':[{'model_code':model,'wip':wip if model=='M' else 0,'net_balance':balance if model=='M' else 0} for model in ['M','N','P']]}


def reset_operation(balance=600,**extra):
    return {'kind':'weekly_reset','source_adoption':1,'snapshot':reset_snapshot(balance),
        'scope':{'start_date':'2026-10-12','end_date':'2027-01-24','line_codes':['L','B'],'model_codes':[]},**extra}


def seed_reset_source(client,plans):
    from auto_plan.plan_models import ExecutionCoverage
    plans.context=plans.context.model_copy(update={'coverage':[ExecutionCoverage(start_date='2026-10-12',end_date='2026-10-18',line_codes=['L','B'],model_codes=['M','N','P'],blanks_are_zero=True)]})
    rows=[row('early',day='2026-10-15',quantity=1000)|{'hours':'24'},row('late',day='2026-10-16',quantity=1000)|{'hours':'24'}]
    response=post(client,'adopt',current_command(client,{'kind':'patch_plan','segments':rows},{'kind':'record_execution','rows':[],'coverage':[{'start_date':'2026-10-12','end_date':'2026-10-14','line_codes':['L','B'],'model_codes':['M','N','P'],'blanks_are_zero':True}],'cutoff':'2026-10-14T08:00:00+08:00'}))
    assert response.status_code==200,response.text


def test_reset_uses_fixed_original_source_and_correction_replaces_balance_instead_of_rededucting(shared_client):
    client,plans,_=shared_client
    seed_reset_source(client,plans)
    checked=preview(client,reset_operation())
    assert checked.status_code==200,checked.text
    ledger=checked.json()['reset_preview']['targets'][0]
    assert (ledger['source_input'],ledger['target'],ledger['actual'],ledger['remaining_target'],ledger['unmet_increase'],ledger['unmet_reduction'])==(2000,1400,0,1400,0,0)
    assert [(r['id'],r['quantity']) for r in checked.json()['plan']['segments']]==[('early',1000),('late',400)]
    first=post(client,'adopt',current_command(client,reset_operation()))
    assert first.status_code==200,first.text
    historic=client.get('/auto_plan/api/plans/adoptions/2').json()
    corrected=reset_operation(500,correction=True)
    checked=preview(client,corrected)
    assert checked.status_code==200,checked.text
    assert checked.json()['reset_preview']['source_adoption']==1
    assert checked.json()['reset_preview']['comparison_adoption']==2
    assert checked.json()['reset_preview']['targets'][0]['target']==1500
    assert sum(r['quantity'] for r in checked.json()['plan']['segments'])==1500
    assert post(client,'adopt',current_command(client,corrected)).status_code==200
    assert client.get('/auto_plan/api/plans/adoptions/2').json()==historic
    repeated=post(client,'adopt',current_command(client,reset_operation()))
    assert repeated.status_code==200,repeated.text
    assert repeated.json()['already_effective'] is True
    assert repeated.json()['effective_adoption']==3
    assert sum(r['quantity'] for r in head(client)['plan']['segments'])==1500
    assert head(client)['latest_adoption']==3


def test_current_day_recertifies_remaining_input_instead_of_inventing_proportional_actual_time(shared_client):
    client,_,inputs=shared_client
    from auto_plan.input_models import InputSnapshot
    data=inputs.read().model_dump(mode='json')
    for capacity in data['parameters']['capacities']: capacity['pieces']=2
    inputs.versions[1]=InputSnapshot.model_validate(data)
    initial=patch(row(day='2026-10-14',quantity=2)|{'hours':'18'})
    assert post(client,'save',current_command(client,initial,{'kind':'record_execution','rows':[fact(1)],'cutoff':'2026-10-14T08:00:00+08:00'})).status_code==200
    confirmation={'kind':'confirm_day_capacity','confirmations':[{'line_code':'L','remaining_hours':'1','current_recipe':'R'}],'observed_at':'2026-10-14T08:00:00+08:00'}
    checked=preview(client,confirmation)
    assert checked.status_code==200,checked.text
    assert checked.json()['evaluation']['executable'] is True
    assert checked.json()['evaluation']['days'][0]['production_hours']['exact'] in ('1/2','0.5')


def test_target_includes_actual_once_and_wip_is_output_only(shared_client):
    client,plans,_=shared_client
    from datetime import datetime, timezone
    # Seed while Monday is still future, then advance the real public-service clock.
    client.app.state.clock=lambda:datetime(2026,10,11,tzinfo=timezone.utc)
    from auto_plan.plan_models import ExecutionCoverage
    plans.context=plans.context.model_copy(update={'coverage':[ExecutionCoverage(start_date='2026-10-05',end_date='2026-10-25',line_codes=['L','B'],model_codes=['M','N','P'],blanks_are_zero=True)]})
    rows=[row('today',day='2026-10-14',quantity=1000)|{'hours':'24'},row('future',day='2026-10-15',quantity=1000)|{'hours':'24'}]
    assert post(client,'adopt',current_command(client,{'kind':'patch_plan','segments':rows})).status_code==200
    client.app.state.clock=lambda:datetime(2026,10,14,tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login',json={'username':'EDITOR','password':'company-secret'}).status_code==200
    assert post(client,'save',current_command(client,{'kind':'record_execution','rows':[fact(600)],'coverage':[{'start_date':'2026-10-12','end_date':'2026-10-14','line_codes':['L','B'],'model_codes':['M','N','P'],'blanks_are_zero':True}],'cutoff':'2026-10-14T08:00:00+08:00'})).status_code==200
    checked=preview(client,reset_operation(snapshot=reset_snapshot(600,wip=500)))
    assert checked.status_code==200,checked.text
    target=checked.json()['reset_preview']['targets'][0]
    assert (target['target'],target['actual'],target['remaining_target'],target['planned_remaining'])==(1400,600,800,800)
    assert sum(r['quantity'] for r in checked.json()['evaluation']['output_sources'])==1900
    assert len([r for r in checked.json()['evaluation']['output_sources'] if r['kind']=='wip'])==1


def test_natural_carry_does_not_include_locked_unmet_reduction_and_new_weeks_have_no_source(shared_client):
    client,plans,_=shared_client
    seed_reset_source(client,plans)
    from auto_plan.plan_models import ExecutionCoverage
    plans.context=plans.context.model_copy(update={'coverage':[ExecutionCoverage(start_date='2026-10-12',end_date='2026-10-25',line_codes=['L','B'],model_codes=['M','N','P'],blanks_are_zero=True)]})
    next_week=row('next',day='2026-10-20',quantity=900)|{'hours':'22'}
    assert post(client,'adopt',current_command(client,patch(next_week))).status_code==200
    lock={'kind':'lock_cells','cells':[fact(1000,day='2026-10-15')]}
    assert post(client,'save',current_command(client,lock)).status_code==200
    checked=preview(client,reset_operation(2500,source_adoption=2))
    assert checked.status_code==200,checked.text
    targets=[r for r in checked.json()['reset_preview']['targets'] if r['model_code']=='M']
    assert [(r['target'],r['natural_carry'],r['unmet_reduction']) for r in targets]==[(0,500,1000),(400,0,0)]
    assert checked.json()['reset_preview']['residual_carry'][0]['week']=='2026-10-26'


def test_correction_requires_whole_adjustment_choices_and_retained_addition_stays_above_target(shared_client):
    client,plans,_=shared_client
    seed_reset_source(client,plans)
    assert post(client,'adopt',current_command(client,reset_operation())).status_code==200
    addition=patch(row('late',day='2026-10-16',quantity=500)|{'hours':'13'})
    assert post(client,'adopt',current_command(client,addition)).status_code==200
    assert head(client)['reset_preview']['targets'][0]['planned_remaining']==1500
    assert head(client)['reset_preview']['targets'][0]['unmet_reduction']==100
    checked=preview(client,reset_operation(500,correction=True))
    assert checked.status_code==200,checked.text
    assert checked.json()['adoption_blocked'] is True
    assert checked.json()['reset_preview']['adjustments'][0]['revision']==3
    rejected=post(client,'adopt',current_command(client,reset_operation(500,correction=True)))
    assert rejected.status_code==422
    keep=preview(client,reset_operation(500,correction=True,choices=[{'revision':3,'retain':True}]))
    assert keep.status_code==200,keep.text
    assert sum(r['quantity'] for r in keep.json()['plan']['segments'])==1600
    assert keep.json()['reset_preview']['targets'][0]['unmet_reduction']==100
    retract=preview(client,reset_operation(500,correction=True,choices=[{'revision':3,'retain':False}]))
    assert retract.status_code==200,retract.text
    assert sum(r['quantity'] for r in retract.json()['plan']['segments'])==1500


def test_current_day_declared_witness_cannot_hide_missing_switch_or_bad_stored_hours(shared_client):
    client,_,_=shared_client
    confirmation={'kind':'confirm_day_capacity','confirmations':[{'line_code':'L','remaining_hours':'1','current_recipe':'R'}],'observed_at':'2026-10-14T08:00:00+08:00'}
    scope={'start_date':'2026-10-14','end_date':'2026-10-14','line_codes':['L'],'model_codes':[]}
    fill={'kind':'append_fill','quantity':2,'model_code':'P','line_code':'L','start_date':'2026-10-14','end_date':'2026-10-14','scope':scope}
    response=post(client,'save',current_command(client,{'kind':'record_execution','rows':[fact(0,'P')],'cutoff':'2026-10-14T08:00:00+08:00'},confirmation,fill))
    assert response.status_code==200,response.text
    current=head(client)
    assert current['evaluation']['executable'] is True
    assert len(current['plan']['switches'])==1
    raw=current['plan'].copy();raw['switches']=[]
    checked=preview(client,{'kind':'replace_plan','plan':raw})
    assert checked.status_code==200,checked.text
    assert checked.json()['evaluation']['executable'] is False
    invalid=deepcopy(current['plan']);invalid['segments'][0]['hours']='0'
    checked=preview(client,{'kind':'replace_plan','plan':invalid})
    assert checked.status_code==200,checked.text
    assert checked.json()['evaluation']['executable'] is False


def test_reset_preserves_current_rows_outside_authorized_scope_and_requires_expansion_to_retract_them(shared_client):
    client,plans,_=shared_client
    seed_reset_source(client,plans)
    assert post(client,'save',current_command(client,patch(row('late',day='2026-10-16',quantity=800)|{'hours':'20'}))).status_code==200
    scope={'start_date':'2026-10-15','end_date':'2026-10-15','line_codes':['L'],'model_codes':['M']}
    operation=reset_operation(scope=scope,choices=[{'revision':2,'retain':True}])
    checked=preview(client,operation)
    assert checked.status_code==200,checked.text
    assert next(r for r in checked.json()['plan']['segments'] if r['id']=='late')==row('late',day='2026-10-16',quantity=800)|{'hours':'20'}
    assert next(r for r in checked.json()['plan']['segments'] if r['id']=='early')['quantity']==600
    rejected=preview(client,reset_operation(scope=scope,choices=[{'revision':2,'retain':False}]))
    assert rejected.status_code in (409,422)
    assert '范围' in rejected.json()['detail']


def test_partial_old_source_coverage_stops_deduction_without_treating_absent_cells_as_zero(shared_client):
    client,plans,_=shared_client
    seed_reset_source(client,plans)
    from auto_plan.plan_models import ExecutionCoverage
    # The second week is an explicitly partial imported range. A subsequent
    # adoption must preserve that boundary rather than invent blank zero days.
    plans.context=plans.context.model_copy(update={'coverage':plans.context.coverage+[
        ExecutionCoverage(start_date='2026-10-20',end_date='2026-10-20',line_codes=['L','B'],model_codes=['M'],blanks_are_zero=True)]})
    next_week=row('next',day='2026-10-20',quantity=900)|{'hours':'22'}
    assert post(client,'adopt',current_command(client,patch(next_week))).status_code==200
    checked=preview(client,reset_operation(2500,source_adoption=2))
    assert checked.status_code==200,checked.text
    targets=[r for r in checked.json()['reset_preview']['targets'] if r['model_code']=='M']
    assert len(targets)==1
    assert targets[0]['natural_carry']==500
    residual=checked.json()['reset_preview']['residual_carry'][0]
    assert (residual['week'],residual['quantity'],residual['state'])==('2026-10-19',500,'来源覆盖不完整，新周另行核对排程')
    assert next(r for r in checked.json()['plan']['segments'] if r['id']=='next')['quantity']==900


def test_pre_cutoff_fact_correction_marks_effective_reset_for_review_but_post_cutoff_preserves_snapshot(shared_client):
    client,plans,_=shared_client
    seed_reset_source(client,plans)
    historical=fact(600,day='2026-10-11')
    assert post(client,'save',current_command(client,{'kind':'record_execution','rows':[historical],'cutoff':'2026-10-14T08:00:00+08:00'})).status_code==200
    assert post(client,'adopt',current_command(client,reset_operation())).status_code==200
    old=client.get('/auto_plan/api/plans/adoptions/2').json()
    corrected=post(client,'save',current_command(client,{'kind':'record_execution','rows':[historical|{'quantity':550}],'reason':'周一前报工纠错'}))
    assert corrected.status_code==200,corrected.text
    current=head(client)
    assert current['reset_preview']['pending_review'] is True
    assert any(r['code']=='reset_pending_review' for r in current['operation_issues'])
    assert post(client,'adopt',current_command(client)).status_code==422
    assert current['context']['reset']==reset_snapshot()
    rechecked=post(client,'adopt',current_command(client,reset_operation(correction=True)))
    assert rechecked.status_code==200,rechecked.text
    assert head(client)['reset_preview']['pending_review'] is False
    assert client.get('/auto_plan/api/plans/adoptions/2').json()==old
    current_fact={'kind':'record_execution','rows':[fact(20)],'cutoff':'2026-10-14T08:00:00+08:00'}
    assert post(client,'save',current_command(client,current_fact)).status_code==200
    assert head(client)['reset_preview']['pending_review'] is False
    assert head(client)['context']['reset']==reset_snapshot()


def test_new_segments_do_not_absorb_previously_overplan_or_plan_free_actual(shared_client):
    client,_,_=shared_client
    assert post(client,'save',current_command(client,patch(row(day='2026-10-14',quantity=1000)|{'hours':'24'}),
        {'kind':'record_execution','rows':[fact(1200),fact(100,'N')],'cutoff':'2026-10-14T08:00:00+08:00'})).status_code==200
    checked=preview(client,patch(row('extra',day='2026-10-14',quantity=100)|{'order':2}),patch(row('free','N',day='2026-10-14',quantity=100)|{'order':3}))
    assert checked.status_code==200,checked.text
    execution={r['segment_id']:r for r in checked.json()['execution_segments']}
    assert execution['a']['executed']==1000
    assert execution['extra']['executed']==0 and execution['free']['executed']==0
    assert execution['extra']['remaining']==100 and execution['free']['remaining']==100
    assert checked.json()['execution_surplus']==[fact(200),fact(100,'N')]


def test_expired_input_stays_in_original_target_and_produces_nothing_until_valid_replacement(shared_client):
    client,plans,_=shared_client
    from datetime import datetime, timezone
    from auto_plan.plan_models import ExecutionCoverage
    client.app.state.clock=lambda:datetime(2026,10,12,tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login',json={'username':'EDITOR','password':'company-secret'}).status_code==200
    plans.context=plans.context.model_copy(update={'coverage':[ExecutionCoverage(start_date='2026-10-12',end_date='2026-10-18',line_codes=['L','B'],model_codes=['M','N','P'],blanks_are_zero=True)]})
    rows=[row('monday',day='2026-10-12',quantity=500)|{'hours':'12'},row('future',day='2026-10-15',quantity=1000)|{'hours':'24'}]
    assert post(client,'adopt',current_command(client,{'kind':'patch_plan','segments':rows},
        {'kind':'record_execution','rows':[fact(300,day='2026-10-12')],'cutoff':'2026-10-12T08:00:00+08:00'})).status_code==200
    client.app.state.clock=lambda:datetime(2026,10,14,tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login',json={'username':'EDITOR','password':'company-secret'}).status_code==200
    assert post(client,'save',current_command(client,{'kind':'record_execution','rows':[],'coverage':[{'start_date':'2026-10-12','end_date':'2026-10-14','line_codes':['L','B'],'model_codes':['M','N','P'],'blanks_are_zero':True}],'cutoff':'2026-10-14T08:00:00+08:00'})).status_code==200
    scope={'start_date':'2026-10-15','end_date':'2026-10-15','line_codes':['L'],'model_codes':['M']}
    checked=preview(client,reset_operation(0,scope=scope))
    assert checked.status_code==200,checked.text
    target=checked.json()['reset_preview']['targets'][0]
    assert (target['source_input'],target['target'],target['actual'],target['remaining_target'],target['unmet_increase'])==(1500,1500,300,1200,200)
    assert checked.json()['evaluation']['output_totals']['expected']==1300
    assert checked.json()['reset_preview']['expired'][0]['quantity']==200
    deduction=preview(client,reset_operation(200,scope=scope))
    assert deduction.status_code==200,deduction.text
    assert deduction.json()['reset_preview']['targets'][0]['unmet_increase']==0
    assert deduction.json()['evaluation']['output_totals']['expected']==1300


def test_known_window_initial_recipe_consumes_prior_switch_time_before_actual(shared_client):
    client,_,inputs=shared_client
    from datetime import datetime, timezone
    from auto_plan.input_models import InputSnapshot
    data=inputs.read().model_dump(mode='json')
    data['parameters']['switch_durations']=[{'from_recipe':'R','to_recipe':'S','hours':'1'}]
    data['calendar']['maintenance']=[{'date':'2026-10-12','line_code':'L','hours':23}]
    inputs.versions[1]=InputSnapshot.model_validate(data)
    client.app.state.clock=lambda:datetime(2026,10,12,tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login',json={'username':'EDITOR','password':'company-secret'}).status_code==200
    planned={'kind':'replace_plan','plan':{'initial_states':[{'line_code':'L','recipe_code':'R'}],'segments':[row('s','P','2026-10-12',quantity=1)|{'hours':'0.05'}]}}
    assert post(client,'save',current_command(client,planned,{'kind':'record_execution','rows':[fact(1,'P','2026-10-12')],'cutoff':'2026-10-12T08:00:00+08:00'})).status_code==200
    checked=preview(client,{'kind':'confirm_day_capacity','confirmations':[{'line_code':'L','remaining_hours':'0.9','current_recipe':'S'}],'observed_at':'2026-10-12T08:00:00+08:00'})
    assert checked.status_code==200,checked.text
    assert checked.json()['adoption_blocked'] is True
    assert checked.json()['evaluation']['executable'] is False


def test_reset_unknown_execution_is_not_zero_even_when_all_source_input_is_future(shared_client):
    client,plans,_=shared_client
    seed_reset_source(client,plans)
    # Removing coverage is an explicit correction, not evidence of zero actual.
    assert post(client,'save',current_command(client,{'kind':'record_execution','rows':[],'coverage':[],'reason':'覆盖需重新核实'})).status_code==200
    checked=preview(client,reset_operation())
    assert checked.status_code==200,checked.text
    target=checked.json()['reset_preview']['targets'][0]
    assert target['actual'] is None and target['remaining_target'] is None
    assert target['actual_known'] is False
    assert checked.json()['adoption_blocked'] is True
    assert sum(r['quantity'] for r in checked.json()['plan']['segments'])==2000


def test_external_first_week_reset_is_unavailable_and_later_rollover_keeps_history_outside_active_plan(shared_client):
    client,plans,_=shared_client
    from datetime import datetime, timezone
    from auto_plan.plan_models import ExecutionCoverage
    plans.context=plans.context.model_copy(update={'coverage':[ExecutionCoverage(start_date='2026-10-12',end_date='2026-10-25',line_codes=['L','B'],model_codes=['M','N','P'],blanks_are_zero=True)],
        'lineage':{'external_reset':{'production_week':'2026-10-12','batch_id':'external','already_applied':True,'system_correction_available':False}}})
    rows=[row('old',day='2026-10-15',quantity=1000)|{'hours':'24'},row('new',day='2026-10-20',quantity=900)|{'hours':'22'}]
    assert post(client,'adopt',current_command(client,{'kind':'patch_plan','segments':rows})).status_code==200
    for correction in (False,True):
        rejected=preview(client,reset_operation(correction=correction))
        assert rejected.status_code==422 and '外部' in rejected.json()['detail']
    assert post(client,'save',current_command(client,{'kind':'lock_cells','cells':[fact(1000,day='2026-10-15')]})).status_code==200
    client.app.state.clock=lambda:datetime(2026,10,19,tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login',json={'username':'EDITOR','password':'company-secret'}).status_code==200
    assert post(client,'save',current_command(client,{'kind':'record_execution','rows':[fact(600,day='2026-10-15')],
        'coverage':[{'start_date':'2026-10-19','end_date':'2026-10-19','line_codes':['L','B'],'model_codes':['M','N','P'],'blanks_are_zero':True}],'cutoff':'2026-10-19T08:00:00+08:00'})).status_code==200
    current_snapshot=reset_snapshot(-100,'next-monday',100)|{'production_week':'2026-10-19','cutoff':'2026-10-19T08:00:00+08:00'}
    scope={'start_date':'2026-10-19','end_date':'2027-01-31','line_codes':['L'],'model_codes':[]}
    checked=preview(client,reset_operation(snapshot=current_snapshot,scope=scope))
    assert checked.status_code==200,checked.text
    assert all(r['date']>='2026-10-19' for r in checked.json()['plan']['segments'])
    assert checked.json()['reset_preview']['targets'][0]['target']==1000
    assert checked.json()['context']['locks'][0]['date']=='2026-10-15'
    assert checked.json()['reset_preview']['historical_inputs'][0]['quantity']==1000
    assert sum(r['quantity'] for r in checked.json()['evaluation']['output_sources'] if r['kind']=='wip')==100


def test_cross_window_switch_progress_is_not_assumed_executed_or_repaired(shared_client):
    client,plans,_=shared_client
    from datetime import datetime, timezone
    from auto_plan.plan_models import ExecutionCoverage
    plans.context=plans.context.model_copy(update={'coverage':[ExecutionCoverage(start_date='2026-10-12',end_date='2026-10-25',line_codes=['L','B'],model_codes=['M','N','P'],blanks_are_zero=True)]})
    source={'initial_states':[{'line_code':'L','recipe_code':'R'}],
        'segments':[row('r',day='2026-10-15'),row('s','P','2026-10-20')],
        'switches':[{'id':'cross','line_code':'L','from_recipe':'R','to_recipe':'S','allocations':[{'date':'2026-10-18','order':0,'hours':'0.4'},{'date':'2026-10-19','order':0,'hours':'0.55'}]}]}
    assert post(client,'adopt',current_command(client,{'kind':'replace_plan','plan':source})).status_code==200
    original=client.get('/auto_plan/api/plans/adoptions/1').json()
    client.app.state.clock=lambda:datetime(2026,10,19,tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login',json={'username':'EDITOR','password':'company-secret'}).status_code==200
    assert post(client,'save',current_command(client,{'kind':'record_execution','rows':[],'coverage':[{'start_date':'2026-10-19','end_date':'2026-10-19','line_codes':['L','B'],'model_codes':['M','N','P'],'blanks_are_zero':True}],'cutoff':'2026-10-19T08:00:00+08:00'})).status_code==200
    snapshot=reset_snapshot(0)|{'production_week':'2026-10-19','cutoff':'2026-10-19T08:00:00+08:00'}
    operation=reset_operation(snapshot=snapshot,scope={'start_date':'2026-10-19','end_date':'2027-01-31','line_codes':['L','B'],'model_codes':[]})
    checked=preview(client,operation)
    assert checked.status_code==200,checked.text
    assert checked.json()['evaluation']['executable'] is False
    assert checked.json()['reset_preview']['crossing_switches'][0]['id']=='cross'
    assert checked.json()['reset_preview']['crossing_switches'][0]['allocations']==original['plan']['switches'][0]['allocations']
    assert post(client,'adopt',current_command(client,operation)).status_code==422
    assert client.get('/auto_plan/api/plans/adoptions/1').json()==original


def test_negative_balance_uses_only_original_week_gaps_and_keeps_unmet_separate_from_next_week(shared_client):
    client,plans,_=shared_client
    from auto_plan.plan_models import ExecutionCoverage
    from auto_plan.plan_models import ExecutionContext
    coverage=ExecutionCoverage(start_date='2026-10-12',end_date='2026-10-25',line_codes=['L','B'],model_codes=['M','N','P'],blanks_are_zero=True)
    plans.context=plans.context.model_copy(update={'coverage':[coverage], 'execution':ExecutionContext(coverage=[coverage.model_copy(update={'end_date':date(2026,10,14)})],cutoff='2026-10-14T08:00:00+08:00')})
    rows=[row('one',day='2026-10-15',quantity=800)|{'hours':'20'},row('two',day='2026-10-16',quantity=800)|{'hours':'20'},row('next',day='2026-10-20',quantity=1000)|{'hours':'24'}]
    assert post(client,'adopt',current_command(client,{'kind':'patch_plan','segments':rows})).status_code==200
    scope={'start_date':'2026-10-15','end_date':'2026-10-16','line_codes':['L'],'model_codes':['M']}
    checked=preview(client,reset_operation(-600,scope=scope))
    assert checked.status_code==200,checked.text
    targets=[r for r in checked.json()['reset_preview']['targets'] if r['model_code']=='M']
    assert (targets[0]['target'],targets[0]['planned_remaining'],targets[0]['unmet_increase'],targets[0]['natural_carry'])==(2200,2000,200,0)
    assert targets[1]['target']==1000
    assert next(r for r in checked.json()['plan']['segments'] if r['id']=='next')==rows[-1]


def test_automatic_quantity_and_future_switches_do_not_consume_unconfirmed_today(shared_client):
    client,_,_=shared_client
    scope={'start_date':'2026-10-14','end_date':'2026-10-15','line_codes':['L'],'model_codes':[]}
    initial={'kind':'patch_plan','segments':[row(day='2026-10-14',quantity=100)],'initial_states':[{'line_code':'L','recipe_code':'R'}]}
    assert post(client,'save',current_command(client,initial,{'kind':'record_execution','rows':[fact(0)],'cutoff':'2026-10-14T08:00:00+08:00'})).status_code==200
    change={'kind':'change_quantity','segment_id':'a','quantity':200,'end_date':'2026-10-15','scope':scope}
    checked=preview(client,change)
    assert checked.status_code==200,checked.text
    assert sum(r['quantity'] for r in checked.json()['plan']['segments'] if r['date']=='2026-10-14')==100
    assert sum(r['quantity'] for r in checked.json()['plan']['segments'] if r['date']=='2026-10-15')==100
    fill={'kind':'append_fill','model_code':'P','quantity':100,'line_code':'L','start_date':'2026-10-14','end_date':'2026-10-15','scope':scope}
    checked=preview(client,fill)
    assert checked.status_code==200,checked.text
    assert all(a['date']>='2026-10-15' for s in checked.json()['plan']['switches'] for a in s['allocations'])


def test_retracting_executed_post_reset_move_conflicts_and_retaining_it_preserves_current_target(shared_client):
    client,plans,_=shared_client
    seed_reset_source(client,plans)
    assert post(client,'adopt',current_command(client,reset_operation())).status_code==200
    scope={'start_date':'2026-10-14','end_date':'2026-10-18','line_codes':['L'],'model_codes':[]}
    split={'kind':'split_segment','segment_id':'late','quantity':100,'target_date':'2026-10-14','target_line':'L','target_order':0,'scope':scope}
    assert post(client,'adopt',current_command(client,split)).status_code==200
    moved=next(r for r in head(client)['plan']['segments'] if r['date']=='2026-10-14')
    assert post(client,'save',current_command(client,{'kind':'record_execution','rows':[fact(100)],'cutoff':'2026-10-14T08:00:00+08:00'})).status_code==200
    rejected=preview(client,reset_operation(500,correction=True,choices=[{'revision':3,'retain':False}]))
    assert rejected.status_code==409,rejected.text
    assert any(r['code']=='reset_protected_adjustment' for r in rejected.json()['conflicts'])
    kept=preview(client,reset_operation(500,correction=True,choices=[{'revision':3,'retain':True}]))
    assert kept.status_code==200,kept.text
    assert next(r for r in kept.json()['plan']['segments'] if r['id']==moved['id'])['quantity']==100
    target=kept.json()['reset_preview']['targets'][0]
    assert (target['target'],target['actual'],target['planned_remaining'],target['unmet_increase'],target['unmet_reduction'])==(1500,100,1400,0,0)


def test_fact_and_wip_only_changes_report_capacity_rounding_and_demand_dependency_keys(shared_client):
    client,plans,_=shared_client
    seed_reset_source(client,plans)
    checked=preview(client,{'kind':'record_execution','rows':[fact(20)],'cutoff':'2026-10-14T08:00:00+08:00'})
    assert checked.status_code==200,checked.text
    effects=checked.json()['effects']
    assert ['CAP','L','2026-10-14'] in effects
    assert ['CELL','2026-10-14','B','M'] in effects
    assert ['REQ','M','2026-10-12'] in effects
    assert post(client,'adopt',current_command(client,reset_operation())).status_code==200
    checked=preview(client,reset_operation(600,correction=True,snapshot=reset_snapshot(600,wip=500)))
    assert checked.status_code==200,checked.text
    assert ['REQ','M','2026-10-12'] in checked.json()['effects']


def test_stability_does_not_consume_new_unexecuted_ids_with_old_surplus_actual(shared_client):
    client,_,_=shared_client
    zero={'kind':'record_execution','rows':[fact(0)],'cutoff':'2026-10-14T08:00:00+08:00'}
    initial=patch(row(day='2026-10-14',quantity=1000)|{'hours':'24'})
    assert post(client,'adopt',current_command(client,initial,zero)).status_code==200
    assert post(client,'save',current_command(client,{'kind':'record_execution','rows':[fact(1200)],'reason':'累计报工更新'})).status_code==200
    checked=preview(client,patch(row('new',day='2026-10-14',quantity=100)|{'order':2}))
    assert checked.status_code==200,checked.text
    week=checked.json()['evaluation']['stability']['weeks'][0]
    assert (week['old_remaining'],week['new_remaining'],week['absolute_change'])==(0,100,100)
    assert checked.json()['evaluation']['stability']['weighted_cost']=='300'
    assert checked.json()['evaluation']['output_totals']['expected']==1300


def test_legacy_facts_bind_to_current_source_before_preview_and_persist_once_on_save(shared_client):
    from auto_plan.plan_models import ExecutionContext
    client,plans,_=shared_client
    assert post(client,'save',current_command(client,patch(row(day='2026-10-14',quantity=600)|{'hours':'15'}))).status_code==200
    historical=client.get('/auto_plan/api/plans/revisions/1').json()
    # Existing deployment data predates server-managed association fields.
    plans.context=plans.context.model_copy(update={'execution':ExecutionContext(facts=[fact(900)],cutoff='2026-10-14T08:00:00+08:00')})
    before=head(client)
    action=patch(row('new',day='2026-10-14',quantity=300)|{'order':2,'hours':'8'})
    checked=preview(client,action)
    assert checked.status_code==200,checked.text
    assert checked.json()['execution_segments'][-1]=={'segment_id':'new','executed':0,'remaining':300}
    assert checked.json()['evaluation']['output_totals']['expected']==1200
    assert head(client)==before
    assert post(client,'save',current_command(client,action)).status_code==200
    current=head(client)
    assert current['input_version']==before['input_version']+1
    assert current['context']['execution']['association_cells']==[fact(900)]
    assert current['context']['execution']['audit']==[]
    assert current['execution_segments'][-1]['remaining']==300
    assert client.get('/auto_plan/api/plans/revisions/1').json()==historical
    assert post(client,'save',current_command(client,{'kind':'patch_plan'})).status_code==200
    assert head(client)['input_version']==current['input_version']


def test_association_correction_cannot_clear_or_omit_recorded_positive_cells(shared_client):
    client,_,_=shared_client
    rows=[row('one',day='2026-10-14',quantity=500)|{'hours':'12'},row('two',day='2026-10-14',quantity=500)|{'order':1,'hours':'12'},row('other','N',day='2026-10-14',line='B',quantity=100)]
    assert post(client,'save',current_command(client,{'kind':'patch_plan','segments':rows},
        {'kind':'record_execution','rows':[fact(600),fact(50,'N',line='B')],'cutoff':'2026-10-14T08:00:00+08:00'})).status_code==200
    before=head(client)
    for associations in ([],[{'segment_id':'one','quantity':100},{'segment_id':'two','quantity':500}]):
        rejected=preview(client,{'kind':'record_execution','associations':associations,'reason':'复核关联'})
        assert rejected.status_code==422,rejected.text
        assert '总量' in rejected.json()['detail']
        assert head(client)==before
    corrected={'kind':'record_execution','associations':[{'segment_id':'one','quantity':100},{'segment_id':'two','quantity':500},{'segment_id':'other','quantity':50}],'reason':'按报工重分段'}
    assert post(client,'save',current_command(client,corrected)).status_code==200
    assert next(r for r in head(client)['execution_segments'] if r['segment_id']=='two')['executed']==500
    cleared={'kind':'record_execution','rows':[fact(0),fact(0,'N',line='B')],'associations':[],'reason':'撤销错误报工'}
    assert post(client,'save',current_command(client,cleared)).status_code==200
    assert all(r['executed']==0 for r in head(client)['execution_segments'])


def test_explicit_audited_association_can_reclassify_surplus_without_default_absorption(shared_client):
    client,_,_=shared_client
    assert post(client,'save',current_command(client,patch(row(day='2026-10-14',quantity=600)|{'hours':'15'}),
        {'kind':'record_execution','rows':[fact(900)],'cutoff':'2026-10-14T08:00:00+08:00'})).status_code==200
    assert post(client,'save',current_command(client,patch(row('new',day='2026-10-14',quantity=300)|{'order':2,'hours':'8'}))).status_code==200
    before=head(client)
    assert before['evaluation']['output_totals']['expected']==1200
    corrected={'kind':'record_execution','associations':[{'segment_id':'a','quantity':600},{'segment_id':'new','quantity':300}],'reason':'确认原计划外报工对应新补录生产段'}
    result=post(client,'save',current_command(client,corrected))
    assert result.status_code==200,result.text
    after=head(client)
    assert after['execution_segments'][-1]['executed']==300
    assert after['execution_surplus']==[]
    assert after['evaluation']['output_totals']['expected']==900
    assert after['context']['execution']['audit'][-1]['changes'][0]['family']=='associations'
    assert post(client,'save',current_command(client,corrected)).status_code==200
    assert head(client)['input_version']==after['input_version']


def test_reset_correction_keeps_or_retracts_the_whole_explicit_idle_adjustment(shared_client):
    client,plans,_=shared_client
    seed_reset_source(client,plans)
    assert post(client,'adopt',current_command(client,reset_operation())).status_code==200
    idle={'date':'2026-10-17','line_code':'L','hours':'1'}
    assert post(client,'adopt',current_command(client,{'kind':'patch_plan','ordinary_idle':[idle]})).status_code==200
    retained=preview(client,reset_operation(500,correction=True,choices=[{'revision':3,'retain':True}]))
    assert retained.status_code==200,retained.text
    assert retained.json()['plan']['ordinary_idle']==[idle]
    retracted=preview(client,reset_operation(500,correction=True,choices=[{'revision':3,'retain':False}]))
    assert retracted.status_code==200,retracted.text
    assert retracted.json()['plan']['ordinary_idle']==[]


def test_execution_cutoff_corrections_require_aware_time_and_a_reason_for_retraction(shared_client):
    client,_,_=shared_client
    assert post(client,'save',current_command(client,{'kind':'record_execution','rows':[fact(0)],'cutoff':'2026-10-14T08:00:00+08:00'})).status_code==200
    before=head(client)
    invalid=preview(client,{'kind':'record_execution','cutoff':'2026-10-14T07:00:00','reason':'调整截止'})
    assert invalid.status_code==422,invalid.text
    rejected=preview(client,{'kind':'record_execution','cutoff':'2026-10-14T07:00:00+08:00'})
    assert rejected.status_code==422 and '原因' in rejected.json()['detail']
    assert head(client)==before


def test_confirmed_today_can_precede_the_same_preserved_future_recipe_from_unknown_old_state(shared_client):
    client,_,_=shared_client
    assert post(client,'save',current_command(client,patch(row(day='2026-10-15',quantity=1000)|{'hours':'24'}),
        {'kind':'record_execution','rows':[fact(0)],'cutoff':'2026-10-14T08:00:00+08:00'})).status_code==200
    scope={'start_date':'2026-10-14','end_date':'2026-10-14','line_codes':['L'],'model_codes':[]}
    confirmation={'kind':'confirm_day_capacity','confirmations':[{'line_code':'L','remaining_hours':'1','current_recipe':'R'}],'observed_at':'2026-10-14T08:00:00+08:00'}
    fill={'kind':'append_fill','quantity':20,'model_code':'M','line_code':'L','start_date':'2026-10-14','end_date':'2026-10-14','scope':scope}
    result=preview(client,confirmation,fill)
    assert result.status_code==200,result.text
    assert sum(r['quantity'] for r in result.json()['plan']['segments'] if r['date']=='2026-10-14')==20
    assert next(r for r in result.json()['plan']['segments'] if r['id']=='a')['quantity']==1000
    assert result.json()['evaluation']['executable'] is True
