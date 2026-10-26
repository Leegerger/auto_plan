from tests.test_plan_transactions import shared_client, command, post, row


def scope(start='2026-10-15',end='2026-10-16',lines=None):
    return {'start_date':start,'end_date':end,'line_codes':lines or ['L'],'model_codes':[]}


def test_append_fills_only_selected_dates_and_line_and_retains_saved_remainder(shared_client):
    client,plans,inputs=shared_client
    data=inputs.read().model_dump(mode='json')
    for capacity in data['parameters']['capacities']: capacity['pieces']=3000
    from auto_plan.input_models import InputSnapshot
    inputs.versions[1]=InputSnapshot.model_validate(data)
    operation={'kind':'append_fill','action_id':'fill-first','model_code':'M','quantity':10000,'line_code':'L',
               'start_date':'2026-10-15','end_date':'2026-10-16','scope':scope()}
    preview=post(client,'preview',{k:v for k,v in command(operation).items() if k not in ('operation_id','pending')})
    assert preview.status_code==200,preview.text
    result=preview.json()
    assert [(r['date'],r['line_code'],r['quantity']) for r in result['plan']['segments']]==[('2026-10-15','L',3000),('2026-10-16','L',3000)]
    assert result['unallocated'][0]['quantity']==4000
    assert post(client,'save',command(operation)).status_code==200
    saved=client.get('/auto_plan/api/plans/current').json()
    assert saved['unallocated'][0]['quantity']==4000
    assert post(client,'save',command(base=1)).status_code==200
    assert client.get('/auto_plan/api/plans/current').json()['unallocated']==saved['unallocated']


def preview(client,*operations,base=0,version=1,adoption=0):
    return post(client,'preview',{k:v for k,v in command(*operations,base=base,version=version,adoption=adoption).items() if k not in ('operation_id','pending')})


def test_swap_is_one_action_and_quantities_follow_models_across_lines(shared_client):
    client,_,_=shared_client
    initial={'kind':'patch_plan','segments':[row(quantity=600)|{'hours':'15'},row('b','N',line='B',quantity=300)|{'hours':'8'}]}
    assert post(client,'save',command(initial)).status_code==200
    action={'kind':'swap_segments','action_id':'swap','segment_ids':['a','b'],'scope':scope(lines=['L','B'])}
    result=preview(client,action,base=1)
    assert result.status_code==200,result.text
    assert {(r['line_code'],r['model_code'],r['quantity']) for r in result.json()['plan']['segments']}=={('L','N',300),('B','M',600)}
    partial=preview(client,action|{'segment_ids':['a']},base=1)
    assert partial.status_code==422
    assert client.get('/auto_plan/api/plans/current').json()['plan']['segments'][0]['quantity']==600


def test_move_split_clear_reorder_preserve_total_and_are_atomic(shared_client):
    client,_,_=shared_client
    assert post(client,'save',command({'kind':'patch_plan','segments':[row(quantity=600)|{'hours':'15'},row('b','N',quantity=300)|{'order':2,'hours':'8'}]})).status_code==200
    split={'kind':'split_segment','action_id':'split','segment_id':'a','quantity':200,'target_date':'2026-10-16','target_line':'B','target_order':0,'scope':scope(lines=['L','B'])}
    result=preview(client,split,base=1)
    assert result.status_code==200,result.text
    segments=result.json()['plan']['segments']
    assert sum(r['quantity'] for r in segments if r['model_code']=='M')==600
    assert [(r['date'],r['quantity']) for r in segments if r['model_code']=='M']==[('2026-10-15',400),('2026-10-16',200)]
    assert preview(client,split,base=1).json()['plan']==result.json()['plan']
    moved=preview(client,{'kind':'move_segment','segment_id':'a','target_date':'2026-10-16','target_line':'B','target_order':0,'scope':scope(lines=['L','B'])},base=1)
    assert moved.status_code==200,moved.text
    assert sum(r['quantity'] for r in moved.json()['plan']['segments'])==900
    ordered=preview(client,{'kind':'reorder_segments','date':'2026-10-15','line_code':'L','segment_ids':['b','a'],'scope':scope()},base=1)
    assert ordered.status_code==200,ordered.text
    assert [(r['id'],r['quantity']) for r in ordered.json()['plan']['segments']]==[('b',300),('a',600)]
    partial=preview(client,{'kind':'reorder_segments','date':'2026-10-15','line_code':'L','segment_ids':['b'],'scope':scope()},base=1)
    assert partial.status_code==422
    cleared=post(client,'save',command({'kind':'clear_segments','segment_ids':['b'],'scope':scope()},base=1))
    assert cleared.status_code==200,cleared.text
    assert len(client.get('/auto_plan/api/plans/current').json()['plan']['segments'])==1
    assert preview(client,{'kind':'append_fill','model_code':'N','quantity':1,'line_code':'L','start_date':'2026-10-15','end_date':'2026-10-15','scope':scope()},base=2).json()['unallocated']==[]


def test_copy_is_additive_mapped_with_day_gaps_and_overwrite_is_explicit(shared_client):
    client,_,_=shared_client
    initial={'kind':'patch_plan','segments':[row(quantity=100),row('second','N',day='2026-10-17',quantity=200),row('existing','N',day='2026-10-19',line='B',quantity=300)]}
    assert post(client,'save',command(initial)).status_code==200
    action={'kind':'copy_segments','action_id':'copy','segment_ids':['a','second'],'target_start_date':'2026-10-19','line_mapping':{'L':'B'},'scope':scope('2026-10-15','2026-10-21',['L','B'])}
    result=preview(client,action,base=1)
    assert result.status_code==200,result.text
    added=[r for r in result.json()['plan']['segments'] if r['id'] not in ['a','second','existing']]
    assert [(r['date'],r['line_code'],r['model_code'],r['quantity']) for r in added]==[('2026-10-19','B','M',100),('2026-10-21','B','N',200)]
    assert any(r['id']=='existing' for r in result.json()['plan']['segments'])
    assert preview(client,action,base=1).json()['plan']==result.json()['plan']
    overwritten=preview(client,action|{'kind':'overwrite_copy_segments'},base=1)
    assert overwritten.status_code==200,overwritten.text
    assert not any(r['id']=='existing' for r in overwritten.json()['plan']['segments'])
    assert preview(client,action|{'line_mapping':{}},base=1).status_code==422


def test_continue_and_discard_only_named_saved_unallocated_input(shared_client):
    client,_,inputs=shared_client
    data=inputs.read().model_dump(mode='json')
    for capacity in data['parameters']['capacities']: capacity['pieces']=3000
    from auto_plan.input_models import InputSnapshot
    inputs.versions[1]=InputSnapshot.model_validate(data)
    fill={'kind':'append_fill','action_id':'fill','model_code':'M','quantity':10000,'line_code':'L','start_date':'2026-10-15','end_date':'2026-10-16','scope':scope()}
    assert post(client,'save',command(fill)).status_code==200
    result_id=client.get('/auto_plan/api/plans/current').json()['unallocated'][0]['id']
    continued=fill|{'action_id':'continue','line_code':'B','quantity':4000,'continue_id':result_id,'scope':scope(lines=['B'])}
    result=preview(client,continued,base=1)
    assert result.status_code==200,result.text
    assert result.json()['unallocated']==[]
    assert sum(r['quantity'] for r in result.json()['plan']['segments'] if r['line_code']=='B')==4000
    discarded=post(client,'save',command({'kind':'discard_unallocated','result_ids':[result_id],'scope':scope()},base=1))
    assert discarded.status_code==200,discarded.text
    assert client.get('/auto_plan/api/plans/current').json()['unallocated']==[]
    assert preview(client,continued,base=2).status_code==422


def test_manual_actions_preserve_outside_scope_and_protect_execution_and_zero_locks(shared_client):
    client,plans,_=shared_client
    initial={'kind':'patch_plan','segments':[row(quantity=600)|{'hours':'15'},row('b','N',quantity=300)|{'order':1,'hours':'8'},row('outside','N',day='2026-10-19')|{'order':7,'hours':'7/2'}]}
    assert post(client,'save',command(initial)).status_code==200
    old=client.get('/auto_plan/api/plans/current').json()['plan']
    result=preview(client,{'kind':'reorder_segments','date':'2026-10-15','line_code':'L','segment_ids':['b','a'],'scope':scope()},base=1)
    assert result.status_code==200,result.text
    assert next(r for r in result.json()['plan']['segments'] if r['id']=='outside')==old['segments'][-1]
    from auto_plan.plans import PlanningContext
    plans.context=PlanningContext.model_validate({'execution':{'facts':[{'date':'2026-10-15','line_code':'L','model_code':'M','quantity':60}],'cutoff':'2026-10-15T09:00:00+08:00'},'locks':[{'date':'2026-10-16','line_code':'B','model_code':'M','quantity':0}]})
    assert preview(client,{'kind':'move_segment','segment_id':'a','target_date':'2026-10-16','target_line':'B','target_order':0,'scope':scope(lines=['L','B'])},base=1).status_code==409
    copied=preview(client,{'kind':'copy_segments','segment_ids':['a'],'target_start_date':'2026-10-16','line_mapping':{'L':'B'},'scope':scope(lines=['L','B'])},base=1)
    assert copied.status_code==409
    assert client.get('/auto_plan/api/plans/current').json()['plan']==old


def test_fill_options_use_exact_shared_recipe_capacity_and_only_allowed_lines(shared_client):
    client,_,inputs=shared_client
    data=inputs.read().model_dump(mode='json')
    data['masters']['capabilities']=[{'recipe_code':'R','line_code':'B','allowed':False}]
    from auto_plan.input_models import InputSnapshot
    inputs.versions[1]=InputSnapshot.model_validate(data)
    assert post(client,'save',command({'kind':'patch_plan','segments':[row(quantity=600)|{'hours':'15'}]})).status_code==200
    payload={k:v for k,v in command(base=1).items() if k not in ('operation_id','pending')}
    options=post(client,'fill-options',payload|{'model_code':'N','start_date':'2026-10-15','end_date':'2026-10-15','line_code':'L','scope':scope('2026-10-15','2026-10-15')})
    assert options.status_code==200,options.text
    assert [r['code'] for r in options.json()['lines']]==['L']
    assert options.json()['days']==[{'date':'2026-10-15','line_code':'L','quantity':400}]
    assert client.get('/auto_plan/api/plans/current').json()['revision']==1


def test_copy_conflict_stays_on_target_date_and_can_be_saved_pending(shared_client):
    client,_,inputs=shared_client
    assert post(client,'save',command({'kind':'patch_plan','segments':[row(quantity=100)]})).status_code==200
    data=inputs.read().model_dump(mode='json')
    data['calendar']['holidays']=[{'date':'2026-10-16','holiday':True}]
    from auto_plan.input_models import InputSnapshot
    inputs.versions[1]=InputSnapshot.model_validate(data)
    action={'kind':'copy_segments','segment_ids':['a'],'target_start_date':'2026-10-16','line_mapping':{'L':'B'},'scope':scope(lines=['L','B'])}
    result=preview(client,action,base=1)
    assert result.status_code==200,result.text
    assert [(r['date'],r['line_code']) for r in result.json()['plan']['segments'] if r['id']!='a']==[('2026-10-16','B')]
    assert result.json()['evaluation']['capacity_valid'] is False
    assert any(issue['date']=='2026-10-16' and issue['line_code']=='B' for issue in result.json()['evaluation']['issues'])
    assert post(client,'save',command(action,base=1)).status_code==200


def test_model_limited_fill_preserves_other_models_exact_hours_and_order(shared_client):
    client,_,_=shared_client
    existing=row('fixed','N',quantity=200)|{'order':0,'hours':'5'}
    assert post(client,'save',command({'kind':'patch_plan','segments':[existing]})).status_code==200
    action={'kind':'append_fill','model_code':'M','quantity':100,'line_code':'L','start_date':'2026-10-15','end_date':'2026-10-15','scope':scope('2026-10-15','2026-10-15')|{'model_codes':['M']}}
    result=preview(client,action,base=1)
    assert result.status_code==200,result.text
    assert next(r for r in result.json()['plan']['segments'] if r['id']=='fixed')==existing
    assert result.json()['unallocated']==[]
    assert result.json()['evaluation']['capacity_valid'] is True


def test_week_codes_and_lossless_hour_display_are_provided_by_server(shared_client):
    client,_,_=shared_client
    current=client.get('/auto_plan/api/plans/current').json()
    assert len(current['weeks'])==15
    assert (current['weeks'][0]['label'],current['weeks'][0]['start_date'])==('W202642','2026-10-12')
    assert current['weeks'][-1]['dates'][-1]=='2027-01-24'
    result=preview(client,{'kind':'patch_plan','segments':[row(quantity=1)|{'hours':'1/1000000'}]})
    assert result.status_code==200,result.text
    assert result.json()['hour_display']['segments']['a']=={'exact':'1/1000000','display':'<0.0001'}


def test_append_does_not_recertify_independent_unchanged_days_in_a_wide_scope(shared_client):
    client,_,_=shared_client
    existing=row('independent','N',day='2026-10-16',quantity=100)|{'order':7,'hours':'3'}
    assert post(client,'save',command({'kind':'patch_plan','segments':[existing]})).status_code==200
    assert post(client,'save',command({'kind':'patch_plan','segments':[existing|{'quantity':110}]},base=1)).status_code==200
    action={'kind':'append_fill','model_code':'M','quantity':100,'line_code':'L','start_date':'2026-10-15','end_date':'2026-10-15','scope':scope()}
    saved=post(client,'save',command(action,base=1))
    assert saved.status_code==200,saved.text
    assert next(r for r in client.get('/auto_plan/api/plans/current').json()['plan']['segments'] if r['id']=='independent')==existing|{'quantity':110}


def test_append_reports_boundary_expansion_without_rewriting_outside_recipe_work(shared_client):
    client,_,_=shared_client
    initial={'kind':'replace_plan','plan':{'initial_states':[{'line_code':'L','recipe_code':'R'}],'segments':[row('outside','N',day='2026-10-19')]}}
    assert post(client,'save',command(initial)).status_code==200
    old=client.get('/auto_plan/api/plans/current').json()['plan']
    action={'kind':'append_fill','model_code':'P','quantity':10,'line_code':'L','start_date':'2026-10-15','end_date':'2026-10-16','scope':scope()}
    result=preview(client,action,base=1)
    assert result.status_code==422,result.text
    assert '扩大范围' in result.json()['detail']
    assert client.get('/auto_plan/api/plans/current').json()['plan']==old


def test_append_keeps_unrelated_pending_capacity_conflict_and_places_valid_day(shared_client):
    client,_,_=shared_client
    pending=row('pending','N',day='2026-10-16',quantity=1200)|{'hours':'24'}
    assert post(client,'save',command({'kind':'patch_plan','segments':[pending]})).status_code==200
    action={'kind':'append_fill','model_code':'M','quantity':100,'line_code':'L','start_date':'2026-10-15','end_date':'2026-10-15','scope':scope()}
    result=preview(client,action,base=1)
    assert result.status_code==200,result.text
    assert result.json()['unallocated']==[]
    assert next(r for r in result.json()['plan']['segments'] if r['id']=='pending')==pending
    assert result.json()['evaluation']['capacity_valid'] is False
    assert any(r['quantity']==100 and r['date']=='2026-10-15' for r in result.json()['plan']['segments'])
