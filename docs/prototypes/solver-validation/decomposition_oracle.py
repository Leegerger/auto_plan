"""Tiny exhaustive audit of Recipe-block quantity optimization, before trim.

This checks the modeled block objective. It is not an oracle for the full
hybrid algorithm, global scheduling, or the general surplus business rule.
"""
from copy import deepcopy
from itertools import product
from pathlib import Path
import json

from decomposition_experiment import validate,repair
from quantity_experiment import materialize,model_data
from order_experiment import transitions


def fixture(split,ct,yield_value,demand):
    days=['2026-10-12','2026-10-13']
    snapshot={'start_date':days[0],'days':105,'holidays':[],
        'lines':[0],'models':[0,1],'model_recipe':{'0':0,'1':int(split)},
        'allowed_lines':{'0':[0],'1':[0]},'switch_hours':{'0:1':'1','1:0':'1'},
        'capacity_by_month':{'2026-10':{'0:0':4,'0:1':4}},
        'line_yield_by_month':{'2026-10':'1'},
        'model_yield_by_month':{'2026-10':{'0':yield_value,'1':yield_value}},
        'ct':{'0':ct,'1':ct},'confirmation_batches':[],
        'demands':[{'model':m,'week':0,'quantity':demand} for m in (0,1)],
        'plan':[{'date':day,'line':0,'model':m,'position':m,'quantity':2} for day in days for m in (0,1)],
        'day_lines':[]}
    state=None
    for day in days:
        rows=[r for r in snapshot['plan'] if r['date']==day]
        events=transitions(snapshot,state,rows)
        snapshot['day_lines'].append({'date':day,'line':0,'initial_recipe':state,
            'available_hours':24,'maintenance_hours':0,'transitions':events})
        state=int(split)
    snapshot['baseline_plan']=deepcopy(snapshot['plan'])
    return materialize(snapshot,[2]*4,set(range(4)))


def main():
    records=[]
    for split,ct,yield_value,demand in product((False,True),('0','1/2'),('1','3/4'),(3,4)):
        snapshot=fixture(split,ct,yield_value,demand)
        mutable=set(range(4))
        free=[i for i,r in enumerate(snapshot['plan']) if snapshot['model_recipe'][str(r['model'])]==0]
        cells={(r['date'],r['line']) for r in snapshot['plan']}
        best=None;feasible=0
        # Capacity is 4 per Recipe per day; no segment can exceed 4.
        for values in product(range(1,5),repeat=len(free)):
            q=[2]*4
            for i,value in zip(free,values):q[i]=value
            try:
                candidate=materialize(snapshot,q,mutable)
                vector=validate(snapshot,candidate,cells)
            except (AssertionError,ValueError):continue
            if any(vector[:30]):continue
            feasible+=1
            if best is None or vector<best:best=vector
        q,stage=repair(snapshot,mutable,0,model_data(snapshot,mutable),3)
        actual=validate(snapshot,materialize(snapshot,q,mutable),cells) if q is not None else None
        matched=actual==best
        records.append({'split_recipes':split,'ct':ct,'yield':yield_value,'demand_each':demand,
            'feasible_enumerations':feasible,'enumerated_vector':best,'solver_vector':actual,
            'stage':stage,'matches':matched})
    report={'scope':__doc__,'passed':sum(r['matches'] for r in records),'total':len(records),'cases':records}
    (Path(__file__).parent/'decomposition-oracle-results.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'passed':report['passed'],'total':report['total']}))
    if report['passed']!=report['total']:raise SystemExit(1)


if __name__=='__main__':main()
