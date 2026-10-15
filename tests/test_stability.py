"""Accepted hand calculations through the public frozen business evaluator."""
from copy import deepcopy
from tests.test_plan_preview import snapshot, segment, preview
from tests.test_inputs import inputs_client


def comparable(before, after, *, as_of='2026-10-12T00:00:00+00:00', coverage=True):
    data=snapshot()
    data['time']={'as_of':as_of,'window_start':'2026-10-12'}
    data['plan']={'segments':after}
    data['baseline']={'version':1,'plan':{'segments':before},'coverage':[
        {'start_date':'2026-10-12','end_date':'2027-01-24','line_codes':['L'],'model_codes':['M','N','P'],'blanks_are_zero':True}
    ] if coverage else []}
    for row in data['inputs']['parameters']['capacities']: row['pieces']=100000
    return data


def test_moving_one_thousand_of_ten_thousand_counts_both_dates_and_keeps_locked_denominator(inputs_client):
    old=[segment('a',0,'M',10000,'4','2026-10-13')]
    new=[segment('a',0,'M',9000,'4','2026-10-13'),segment('b',0,'M',1000,'1','2026-10-14')]
    data=comparable(old,new)
    result=preview(inputs_client,data)
    week=result['stability']['weeks'][0]
    assert (week['old_remaining'],week['new_remaining'],week['absolute_change'],week['rate'],week['rate_display'])==(10000,10000,2000,'1/5','20.00%')
    assert result['stability']['weighted_cost']=='6000'
    assert week['exceeds_tolerance'] is True
    assert any(row['code']=='stability_tolerance' and row['severity']=='warning' for row in result['issues'])
    data['locks']=[{'date':'2026-10-13','line_code':'L','model_code':'M','quantity':9000}]
    assert preview(inputs_client,data)['stability']==result['stability']


def test_confirmed_six_thousand_is_removed_once_even_after_production_date_has_passed(inputs_client):
    old=[segment('a',0,'M',10000,'4','2026-10-13')]
    data=comparable(old,[old[0]|{'quantity':9000}],as_of='2026-10-14T10:00:00+00:00')
    data['execution']={'facts':[{'date':'2026-10-13','line_code':'L','model_code':'M','quantity':6000}]}
    week=preview(inputs_client,data)['stability']['weeks'][0]
    assert (week['old_remaining'],week['new_remaining'],week['absolute_change'],week['rate'])==(4000,3000,1000,'1/4')
    data['execution']={'coverage':[{'start_date':'2026-10-13','end_date':'2026-10-13','line_codes':['L'],'model_codes':['M'],'blanks_are_zero':True}]}
    assert preview(inputs_client,data)['stability']['weeks'][0]['old_remaining']==10000
    data['execution']={}
    week=preview(inputs_client,data)['stability']['weeks'][0]
    assert (week['state'],week['rate'],week['unknown_execution_cells'])==('unknown_execution',None,1)


def test_new_plan_zero_baseline_and_partial_coverage_remain_distinct(inputs_client):
    data=comparable([],[])
    assert preview(inputs_client,data)['stability']['weeks'][0]['rate_display']=='0.00%'
    data['plan']['segments']=[segment('a',0,'M',500,'1','2026-10-13')]
    result=preview(inputs_client,data)['stability']
    assert (result['weeks'][0]['state'],result['weeks'][0]['rate'],result['weeks'][0]['added'],result['weighted_cost'])==('zero_baseline',None,500,'1500')
    data['baseline']['coverage']=[]
    result=preview(inputs_client,data)['stability']
    assert (result['weeks'][0]['state'],result['weeks'][0]['rate_display'],result['weighted_cost'])==('new_plan','新排计划','0')
    data['baseline']['plan']['segments']=deepcopy(data['plan']['segments'])
    data['plan']['segments'][0]['quantity']=450
    result=preview(inputs_client,data)['stability']
    assert (result['weeks'][0]['state'],result['weeks'][0]['rate'],result['weighted_cost'])==('partial','1/10','150')
    assert '覆盖不完整' in result['weeks'][0]['rate_display']


def test_pairwise_order_uses_all_interleaved_relations_and_shared_remaining_models(inputs_client):
    def sequence(names):
        return [segment(str(i),i,name,100,'1','2026-10-13') for i,name in enumerate(names)]
    cases=[(['M','N'],['N','M'],1),(['M','N'],['M','M','N'],0),
           (['M','N'],['M','N','M'],1),(['M','N','M'],['N','M','N'],0),
           (['M','N','P'],['P','N','M'],3),(['M','N','P'],['M','P'],0),
           (['M','N'],['M','P','N'],0)]
    for old,new,expected in cases:
        data=comparable(sequence(old),sequence(new))
        assert preview(inputs_client,data)['stability']['order_change_count']==expected,(old,new)
    data=comparable(sequence(['M','N']),sequence(['N','M']))
    data['execution']={'facts':[{'date':'2026-10-13','line_code':'L','model_code':'M','quantity':100}]}
    assert preview(inputs_client,data)['stability']['order_change_count']==0


def test_each_actual_week_uses_editable_split_tolerance_and_exact_weights(inputs_client):
    old=[segment('a',0,'M',10000,'4','2026-11-03'),segment('b',0,'M',20000,'5','2026-11-10')]
    data=comparable(old,[old[0]|{'quantity':9000},old[1]|{'quantity':21000}])
    stability=preview(inputs_client,data)['stability']
    assert [stability['weeks'][i]['rate'] for i in (3,4)]==['1/10','1/20']
    assert [stability['weeks'][i]['exceeds_tolerance'] for i in (3,4)]==[False,False]
    assert stability['weighted_cost']=='4000'
    data['preferences']={'near_weeks':5,'near_tolerance_percent':'4.5','far_tolerance_percent':'50','near_week_weight':'7/2','far_week_weight':'1/2'}
    stability=preview(inputs_client,data)['stability']
    assert stability['weighted_cost']=='7000'
    assert stability['weeks'][4]['band']=='near'
    assert stability['weeks'][4]['exceeds_tolerance'] is True
    data['preferences']['near_week_weight']='1/2'
    assert inputs_client.post('/auto_plan/api/plans/evaluate',json={'snapshot':data}).status_code==422


def test_rolling_window_compares_actual_dates_and_keeps_the_added_week_without_zero_baseline(inputs_client):
    old=[segment('a',0,'M',1000,'1','2026-10-20')]
    new=[old[0]|{'quantity':900},segment('tail',0,'M',200,'1','2027-01-26')]
    data=comparable(old,new,as_of='2026-10-19T00:00:00+00:00')
    data['time']['window_start']='2026-10-19'
    stability=preview(inputs_client,data)['stability']
    assert (stability['weeks'][0]['week'],stability['weeks'][0]['rate'])==('2026-10-19','1/10')
    assert (stability['weeks'][14]['state'],stability['weeks'][14]['rate'])==('new_plan',None)
    assert stability['weighted_cost']=='300'


def test_near_boundary_can_include_the_whole_fifteen_week_window(inputs_client):
    data=comparable([],[])
    data['preferences']={'near_weeks':15}
    result=preview(inputs_client,data)['stability']
    assert all(week['band']=='near' for week in result['weeks'])


def test_frozen_evaluator_enforces_aggregate_and_zero_locks_for_future_solver_candidates(inputs_client):
    data=comparable([], [segment('a',0,'M',60,'1','2026-10-13'),segment('b',1,'M',41,'1','2026-10-13')])
    data['locks']=[{'date':'2026-10-13','line_code':'L','model_code':'M','quantity':100}]
    result=preview(inputs_client,data)
    assert result['executable'] is False
    assert any(issue['code']=='locked_cell' and issue['model_code']=='M' for issue in result['issues'])
    data['locks'][0]['quantity']=0
    data['plan']['segments']=[segment('extra',0,'M',1,'1','2026-10-13')]
    assert any(issue['code']=='locked_cell' for issue in preview(inputs_client,data)['issues'])
