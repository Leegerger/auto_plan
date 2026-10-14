"""Concrete accepted-rule examples and deliberately invalid certificates."""
from copy import deepcopy
from fractions import Fraction as F
from pathlib import Path
import json
from boundary_checks import attribute_and_confirm, check_switch_allocations, check_quantity_protection


def demand(week,quantity):
    return {'model':'M','week':week,'quantity':quantity,
            'start':['2026-10-12','2026-10-19'][week],
            'end':['2026-10-18','2026-10-25'][week]}


def batch(bid,kind,week,quantity,day):
    return {'id':bid,'type':kind,'model':'M','week':week,'quantity':quantity,'date':day}


def source(sid,day,q,outputs,kind='input'):
    return {'id':sid,'date':day,'model':'M','input_quantity':q,'kind':kind,
            'outputs':[{'date':d,'quantity':n} for d,n in outputs]}


def main():
    results = []
    def record(name,actual,expected):
        results.append({'name':name,'actual':actual,'expected':expected,'passed':actual==expected})
    def rejects(name,fn,reason):
        try:
            fn()
            results.append({'name':name,'passed':False,'error':'accepted invalid certificate'})
        except ValueError as error:
            results.append({'name':name,'passed':reason in str(error),'error':str(error),'expected_reason':reason})

    sources=[source('S','2026-10-14',1072,[('2026-10-14',500),('2026-10-15',500)])]
    demands=[demand(0,600),demand(1,400)]
    partial=attribute_and_confirm(sources,demands,[batch('M1','material',1,428,'2026-10-14')])
    record('largest_remainder_643_429',[partial['attribution'][('S',0)],partial['attribution'][('S',1)]],[643,429])
    record('partial_material_399_and_1',[partial['metrics'][('M',1)]['on_time'],partial['metrics'][('M',1)]['pending_material']],[399,1])
    full=attribute_and_confirm(sources,demands,[batch('M1','material',1,300,'2026-10-14'),batch('M2','material',1,129,'2026-10-14')])
    record('additive_batches_300_plus_129',full['metrics'][('M',1)]['on_time'],400)
    shrunk=attribute_and_confirm(sources,demands,[batch('M1','material',1,300,'2026-10-14'),batch('M2','material',1,128,'2026-10-14')])
    record('batch_shrink_one_input_loses_one_output',shrunk['metrics'][('M',1)]['on_time'],399)
    replacement=attribute_and_confirm(sources,demands,[batch('M1','material',1,300,'2026-10-14'),batch('M2','material',1,128,'2026-10-14'),batch('M3','material',1,1,'2026-10-14')])
    record('replacement_batch_rematches',replacement['metrics'][('M',1)]['on_time'],400)
    moved=attribute_and_confirm(sources,[demand(0,500),demand(1,500)],[batch('M1','material',1,429,'2026-10-14')])
    record('ownership_change_rechecks_quota',[moved['attribution'][('S',1)],moved['metrics'][('M',1)]['on_time'],moved['metrics'][('M',1)]['pending_material']],[536,400,100])
    tail=attribute_and_confirm(sources,[demand(0,999)],[])
    record('necessary_rounding_tail_no_quota_discount',[tail['attribution'][('S',0)],tail['unallocated']],[1072,1])
    tiny=[source('T','2026-10-14',1,[('2026-10-14',1),('2026-10-15',1)])]
    zero=attribute_and_confirm(tiny,[demand(0,1),demand(1,1)],[])
    record('zero_input_share_allowed',[zero['attribution'][('T',1)],zero['metrics'][('M',1)]['on_time']],[0,1])
    forbidden=attribute_and_confirm(tiny,[demand(0,1),demand(1,1)],[],[('M',1,'material')])
    record('zero_input_share_does_not_bypass_ban',[forbidden['metrics'][('M',1)]['banned'],bool(forbidden['violations'])],[1,True])
    banned=attribute_and_confirm(sources,demands,[batch('M1','material',1,9999,'2026-10-14')],[('M',1,'material')])
    record('ban_overrides_allowance',[banned['metrics'][('M',1)]['on_time'],banned['metrics'][('M',1)]['banned'],banned['quota_used']['M1']],[0,400,0])
    late=[source('L','2026-10-19',150,[('2026-10-20',150)])]
    pending=attribute_and_confirm(late,[demand(0,100),demand(1,100)],[])
    record('old_week_unconfirmed_not_skipped',[sum(x['quantity'] for x in pending['assignments'] if x['week']==0),sum(x['quantity'] for x in pending['assignments'] if x['week']==1)],[100,50])
    dates=attribute_and_confirm([source('D','2026-10-19',120,[('2026-10-22',120)])],[demand(0,120)],
                               [batch('S1','shipping',0,100,'2026-10-20'),batch('S2','shipping',0,100,'2026-10-23')])
    record('shipping_expiry_not_merged',[dates['metrics'][('M',0)]['late'],dates['metrics'][('M',0)]['pending_shipping'],dates['quota_used']['S1']],[100,20,0])
    pair=[source('Early','2026-10-09',100,[('2026-10-20',100)]),source('Later','2026-10-12',100,[('2026-10-21',100)])]
    shipping=[batch('S','shipping',0,100,'2026-10-23')]
    before=attribute_and_confirm(pair,[demand(0,200)],shipping)
    record('pending_material_does_not_reserve_shipping',[m['source'] for m in before['matches'] if m['type']=='shipping'],['Later'])
    after=attribute_and_confirm(pair,[demand(0,200)],shipping+[batch('M','material',0,100,'2026-10-09')])
    record('new_material_reassigns_shipping_chronologically',[m['source'] for m in after['matches'] if m['type']=='shipping'],['Early'])
    shipping_ban=attribute_and_confirm(late,[demand(0,150)],[batch('S','shipping',0,1000,'2026-10-25')],[('M',0,'shipping')])
    record('shipping_ban_is_hard_violation',[shipping_ban['metrics'][('M',0)]['banned'],shipping_ban['metrics'][('M',0)]['late']],[150,0])
    same_day=[source('New','2026-10-12',10,[('2026-10-12',10)]),source('WIP','2026-10-12',0,[('2026-10-12',10)],'wip')]
    wip=attribute_and_confirm(same_day,[demand(0,10)],[])
    record('same_day_wip_first',[a['source'] for a in wip['assignments']],['WIP'])
    rejects('reject_split_execution_and_plan_sources',lambda:attribute_and_confirm(
        [source('actual','2026-10-14',1,[('2026-10-14',1)]),source('plan','2026-10-14',1,[('2026-10-14',1)])],[demand(0,2)],[]),'aggregated')

    tuesday,wednesday=('2026-10-20',0),('2026-10-21',0)
    event={'id':'AB','line':0,'from':'A','to':'B','after':{'date':tuesday[0],'position':0},
           'before':{'date':wednesday[0],'position':0},
           'allocations':[{'date':tuesday[0],'hours':'0.425'},{'date':wednesday[0],'hours':'0.525'}]}
    calendar={tuesday:1,wednesday:1}
    production={tuesday:'0.575',wednesday:'0.475'}
    durations={('A','B'):'0.95'}
    valid=check_switch_allocations([event],calendar,production,durations,authorized={tuesday,wednesday})
    record('cross_day_exact_0425_0525',[valid['switches'],str(sum(valid['occupied_hours'].values()))],[1,'19/20'])
    rejects('scope_must_expand_for_previous_day_switch',lambda:check_switch_allocations([event],calendar,production,durations,authorized={wednesday}),'expand authorized')
    kept=check_switch_allocations([event],calendar,production,durations,baseline_events=[event],authorized={wednesday})
    record('existing_outside_switch_can_remain',kept['switches'],1)
    invalid=deepcopy(event);invalid['allocations'][0]['hours']='0.424'
    rejects('no_missing_fraction_of_switch',lambda:check_switch_allocations([invalid],calendar,production,durations),'conserved exactly')
    invalid=deepcopy(event);invalid['allocations'][0]['date']='2026-10-19'
    rejects('no_time_before_previous_production',lambda:check_switch_allocations([invalid],calendar,production,durations),'production anchors')
    invalid=deepcopy(event);invalid['allocations'][1]['date']='2026-10-22'
    rejects('no_time_after_target_production',lambda:check_switch_allocations([invalid],calendar,production,durations),'production anchors')
    rejects('holiday_cannot_pay_switch_time',lambda:check_switch_allocations([event],{tuesday:0,wednesday:1},{wednesday:'0.475'},durations),'exceed available')
    duplicate=deepcopy(event);duplicate['id']='AB-again'
    rejects('ordinary_idle_not_reused_for_two_switches',lambda:check_switch_allocations([event,duplicate],calendar,production,durations),'exceed available')
    rejects('duplicate_switch_identity',lambda:check_switch_allocations([event,event],calendar,production,durations),'duplicate switch')
    zero_event=deepcopy(event);zero_event['allocations']=[]
    z=check_switch_allocations([zero_event],calendar,production,{('A','B'):'0'},authorized={wednesday})
    record('zero_hour_recipe_change_counts_once',z['switches'],1)
    rejects('zero_hour_outside_switch_still_needs_scope',lambda:check_switch_allocations([zero_event],calendar,production,{('A','B'):'0'},authorized={tuesday}),'expand authorized')
    key=('2026-10-20',0,'M')
    total=check_quantity_protection({key:40},{key:40},{key:60},{key:100},{key})
    record('executed_plus_remaining_counted_once',total[key],100)
    rejects('aggregate_lock_cannot_change',lambda:check_quantity_protection({key:40},{key:41},{key:60},{key:100},{key}),'locked aggregate')
    rejects('zero_lock_cannot_receive_input',lambda:check_quantity_protection({},{key:1},{},{key:0},{key}),'locked aggregate')
    rejects('outside_quantity_cannot_change',lambda:check_quantity_protection({key:40},{key:39},{key:60},{},set()),'outside authorization')
    report={'scope':__doc__,'checks':results,'passed':sum(r['passed'] for r in results),'total':len(results)}
    (Path(__file__).parent/'boundary-results.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({'passed':report['passed'],'total':report['total'],'failures':[r for r in results if not r['passed']]},ensure_ascii=False))
    return int(report['passed']!=report['total'])


if __name__ == '__main__':
    raise SystemExit(main())
