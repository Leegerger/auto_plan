"""Throwaway construction plus alternating quantity/order neighborhoods.

Existing model/date/line membership and positive segments remain fixed.
Ample confirmations, no execution/WIP/locks; setup charged to target day.
The full objective is rechecked, but no full scheduling proof is claimed.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import date
from fractions import Fraction as F
from math import ceil
from pathlib import Path
from time import perf_counter
import json
import resource
import sys

from quantity_experiment import make_case,model_data,materialize,quantities
from order_experiment import digest,atomic_write,make_graph,solve_cp,load_case
from order_experiment import materialize as materialize_order
from validate_reference import check
from surplus_audit import trim


def validate(snapshot,candidate,mutable_cells):
    for key in snapshot:
        if key not in ('plan','day_lines') and snapshot[key]!=candidate[key]:
            raise ValueError('changed immutable input '+key)
    def group(rows):
        out=defaultdict(list)
        for row in rows:
            out[(row['date'],row['line'])].append(row)
        return {key:sorted(value,key=lambda r:r['position']) for key,value in out.items()}
    old,new,baseline=map(group,(snapshot['plan'],candidate['plan'],snapshot['baseline_plan']))
    if old.keys()!=new.keys():
        raise ValueError('changed date/line membership')
    r=0
    d=0
    start=date.fromisoformat(snapshot['start_date'])
    for key,rows in old.items():
        if sorted(x['model'] for x in rows)!=sorted(x['model'] for x in new[key]):
            raise ValueError('changed active model/segment membership')
        if key not in mutable_cells and rows!=new[key]:
            raise ValueError('changed outside production')
        models=sorted({x['model'] for x in rows})
        weight=3 if (date.fromisoformat(key[0])-start).days<28 else 1
        for a in models:
            d+=weight*abs(sum(x['quantity'] for x in new[key] if x['model']==a)-sum(x['quantity'] for x in baseline[key] if x['model']==a))
        for i,a in enumerate(models):
            for b in models[i+1:]:
                positions=lambda seq,m:[j for j,x in enumerate(seq) if x['model']==m]
                oa,ob=positions(baseline[key],a),positions(baseline[key],b)
                na,nb=positions(new[key],a),positions(new[key],b)
                r+=int((max(oa)<min(ob) and min(nb)<max(na)) or (max(ob)<min(oa) and min(na)<max(nb)))
    cells={(c['date'],c['line']):c for c in candidate['day_lines']}
    if cells.keys()!={(c['date'],c['line']) for c in snapshot['day_lines']}:
        raise ValueError('changed calendar cell set')
    for cell in snapshot['day_lines']:
        key=(cell['date'],cell['line'])
        if key not in mutable_cells and cell!=cells[key]:
            raise ValueError('changed outside setup/hours')
        for field in ('available_hours','maintenance_hours'):
            if cell[field]!=cells[key][field]:
                raise ValueError('changed calendar')
    result=check(candidate)
    return [w['demand']-w['on_time'] for w in result['weeks']]+[w['final_shortage'] for w in result['weeks']]+[result['switches'],d,r]


def construct(snapshot,mutable,data):
    """Find a per-model scale using exact cumulative output, not reference q.

    One-millionth scale steps are a constructor heuristic only. The repair
    model below allows every integer quantity within the exact capacity.
    """
    upper,capacities,slots,deadlines,weights=data
    base=quantities(snapshot)
    result=list(base)
    by_model=defaultdict(list)
    for i,row in enumerate(snapshot['plan']):
        by_model[row['model']].append(i)
    logs=[]
    for m,indices in sorted(by_model.items()):
        selected=[j for j,s in enumerate(slots) if s['source'][1]==m]
        relevant=[(js,need) for js,need in deadlines if js and slots[js[0]]['source'][1]==m]
        def proposal(scale):
            return {i:(ceil(F(scale,1_000_000)*base[i]) if i in mutable else base[i]) for i in indices}
        def covered(values):
            y={j:ceil(F(slots[j]['numerator'],slots[j]['denominator'])*sum(values[i] for i in slots[j]['indices'])) for j in selected}
            return all(sum(y[j] for j in js)>=need for js,need in relevant)
        lo,hi=1_000_000,1_000_000
        while not covered(proposal(hi)) and hi<4_000_000:
            hi*=2
        if not covered(proposal(hi)):
            raise ValueError('constructor did not cover demand; not an infeasibility proof')
        while lo<hi:
            mid=(lo+hi)//2
            if covered(proposal(mid)):hi=mid
            else:lo=mid+1
        for i,value in proposal(lo).items():result[i]=value
        logs.append({'model':m,'scale_numerator':lo,'scale_denominator':1_000_000})
    candidate=materialize(snapshot,result,mutable)
    return candidate,logs


def repair(snapshot,mutable,recipe,data,seconds):
    from ortools.sat.python import cp_model
    upper,capacities,slots,deadlines,weights=data
    current=quantities(snapshot)
    free={i for i in mutable if snapshot['model_recipe'][str(snapshot['plan'][i]['model'])]==recipe}
    if not free:return None,{'recipe':recipe,'status':'no_authorized_rows'}
    baseline={(r['date'],r['line'],r['model']):r['quantity'] for r in snapshot['baseline_plan']}
    if len(baseline)!=len(snapshot['baseline_plan']):
        raise ValueError('this repair experiment assumes unique model per date/line')
    model=cp_model.CpModel()
    q={i:model.new_int_var(1,upper[i],f'q{i}') for i in free}
    for i in free:model.add_hint(q[i],current[i])
    for coefficients,bound in capacities:
        varying=free.intersection(coefficients)
        if varying:
            fixed=sum(a*current[i] for i,a in coefficients.items() if i not in free)
            model.add(sum(coefficients[i]*q[i] for i in varying)<=bound-fixed)
    ys=[]
    for j,slot in enumerate(slots):
        n,b=slot['numerator'],slot['denominator']
        if free.intersection(slot['indices']):
            y=model.new_int_var(0,slot['upper'],f'y{j}')
            numerator=n*sum(q[i] if i in free else current[i] for i in slot['indices'])
            model.add(b*y-b+1<=numerator);model.add(numerator<=b*y)
            model.add_hint(y,ceil(F(n,b)*sum(current[i] for i in slot['indices'])))
            ys.append(y)
        else:ys.append(ceil(F(n,b)*sum(current[i] for i in slot['indices'])))
    for indices,need in deadlines:
        changing=[j for j in indices if not isinstance(ys[j],int)]
        if changing:
            fixed=sum(ys[j] for j in indices if isinstance(ys[j],int))
            model.add(sum(ys[j] for j in changing)>=need-fixed)
    deviations=[]
    for i in sorted(free):
        row=snapshot['plan'][i]
        old=baseline[(row['date'],row['line'],row['model'])]
        d=model.new_int_var(0,max(old,upper[i]),f'd{i}')
        model.add_abs_equality(d,q[i]-old)
        model.add_hint(d,abs(current[i]-old))
        deviations.append(weights[i]*d)
    model.minimize(sum(deviations))
    solver=cp_model.CpSolver()
    solver.parameters.num_search_workers=1
    solver.parameters.random_seed=0
    solver.parameters.max_time_in_seconds=seconds
    status=solver.solve(model)
    valid=status in (cp_model.FEASIBLE,cp_model.OPTIMAL)
    if valid:
        for i in free:current[i]=solver.value(q[i])
    return current if valid else None,{'recipe':recipe,'status':solver.status_name(status),
        'value':solver.objective_value if valid else None,'bound':solver.best_objective_bound,
        'free_quantities':len(free),'variables':len(model.proto.variables),'constraints':len(model.proto.constraints),
        'proof_scope':'this Recipe quantity block with all other quantities and sequence fixed'}


def run(local,variant,path,budget):
    began=perf_counter()
    snapshot,reference,mutable=make_case(local,'uniform5' if variant=='scrambled5' else variant)
    if variant=='scrambled5':
        scrambled,_,_=load_case('scrambled-local' if local else 'scrambled-full')
        snapshot['plan']=scrambled['plan']
        snapshot['day_lines']=scrambled['day_lines']
    cells={(snapshot['plan'][i]['date'],snapshot['plan'][i]['line']) for i in mutable}
    reference_vector=validate(snapshot,reference,cells)
    input_hash=digest({'snapshot':snapshot,'mutable':sorted(mutable)})
    best=None;best_vector=None;first=None;history=[];repairs=[]
    def retain(candidate,stage):
        nonlocal best,best_vector,first
        vector=validate(snapshot,candidate,cells)
        if any(vector[:30]):raise ValueError('candidate failed zero shortage exact check')
        changed=best is None or vector<best_vector
        history.append({'stage':stage,'seconds':perf_counter()-began,'vector':vector,'accepted':changed})
        if changed:
            best,best_vector=candidate,vector
            atomic_write(path.with_suffix('.candidate.json'),{'input':snapshot,'candidate':best,
                'mutable_cells':sorted(cells),'vector':vector,'input_sha256':input_hash,
                'scope':__doc__})
            if first is None:first=perf_counter()-began
        return changed
    data=model_data(snapshot,mutable)
    candidate,scales=construct(snapshot,mutable,data)
    cleaned,audit=trim(snapshot,candidate,mutable)
    retain(materialize(candidate,cleaned,mutable),'constructed_and_trimmed')
    def order_step(label):
        if perf_counter()>=began+budget-6 or path.with_suffix('.stop').exists():return
        graph=make_graph(best,cells)
        selected,stages,size=solve_cp(graph,min(began+budget-4,perf_counter()+4),True)
        repairs.append({'stage':'order','cycle':label,'solver_stages':[{**s,'completed_seconds':s['completed_seconds']-began} for s in stages],'model':size})
        if selected is not None:retain(materialize_order(best,graph,selected),f'order_{label}')
    # Improve the higher-priority switching objective before quantity repair
    # can move production into capacities that block a better order.
    order_step('initial')
    for cycle in range(2):
        if path.with_suffix('.stop').exists():break
        before=list(best_vector)
        active={i for i,r in enumerate(best['plan']) if (r['date'],r['line']) in cells}
        data=model_data(best,active)
        for recipe in sorted(set(snapshot['model_recipe'].values())):
            if perf_counter()>began+budget-6 or path.with_suffix('.stop').exists():break
            q,log=repair(best,active,recipe,data,min(1.0,max(.001,began+budget-6-perf_counter())))
            log['cycle']=cycle;repairs.append(log)
            if q is not None:
                candidate=materialize(best,q,active)
                cleaned,audit=trim(best,candidate,active)
                retain(materialize(candidate,cleaned,active),f'quantity_{cycle}_{recipe}')
        order_step(cycle)
        if best_vector==before or perf_counter()>began+budget-6:break
    report={'variant':variant,'local':local,'input_sha256':input_hash,'budget_seconds':budget,
        'first_candidate_saved_seconds':first,'seconds':perf_counter()-began,'vector':best_vector,
        'reference_vector':reference_vector,'reference_quality_met':best_vector<=reference_vector,
        'history':history,'repairs':repairs,'constructor_scales':scales,'last_surplus_audit':audit,
        'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'scope':__doc__,
        'within_compute_target':perf_counter()-began<=budget,
        'stop_requested':path.with_suffix('.stop').exists(),
        'global_optimality_proven':False,'end_to_end_acceptance':False}
    atomic_write(path,report)
    print(json.dumps({k:report[k] for k in ('variant','local','first_candidate_saved_seconds','seconds','vector','reference_quality_met')}),flush=True)
    return report


if __name__=='__main__':
    case,variant,target=sys.argv[1:4]
    run(case=='local',variant,Path(target),30 if case=='local' else 120)
