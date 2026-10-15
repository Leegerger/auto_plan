"""Dated comparison with the latest adoption, using confirmed facts on both sides."""
from collections import defaultdict
from datetime import timedelta
from fractions import Fraction
from .plan_models import StabilityResult, StabilityWeek
from .production_time import production_date


def _cells(plan):
    result=defaultdict(int)
    for row in plan.segments:
        result[row.date,row.line_code,row.model_code]+=row.quantity
    return result


def _covered(key, coverage):
    day,line,model=key
    return any(row.blanks_are_zero and row.start_date<=day<=row.end_date and line in row.line_codes and model in row.model_codes for row in coverage)


def _percent(value):
    hundredths=(value.numerator*10000*2+value.denominator)//(2*value.denominator)
    return f'{hundredths//100}.{hundredths%100:02d}%'


def _remaining_segments(plan, actual, known):
    assigned=defaultdict(int)
    output=defaultdict(list)
    for row in sorted(plan.segments,key=lambda r:(r.date,r.line_code,r.order,r.id)):
        key=row.date,row.line_code,row.model_code
        if not known(key): continue
        used=min(row.quantity,max(0,actual.get(key,0)-assigned[key]))
        assigned[key]+=used
        if row.quantity>used: output[row.date,row.line_code].append(row.model_code)
    return output


def _order_changes(before, after):
    total=0
    for key in before.keys() & after.keys():
        old,new=before[key],after[key]
        shared=set(old)&set(new)
        old_bounds={};new_bounds={}
        for sequence,bounds in ((old,old_bounds),(new,new_bounds)):
            for i,model in enumerate(sequence):
                bounds.setdefault(model,[i,i])[1]=i
        models=sorted(shared)
        for i,left in enumerate(models):
            for right in models[i+1:]:
                if any(new_bounds[a][0]<new_bounds[b][1] and not old_bounds[a][0]<old_bounds[b][1]
                       for a,b in ((left,right),(right,left))): total+=1
    return total


def compare_stability(snapshot):
    baseline=snapshot.baseline
    prefs=snapshot.preferences
    old=_cells(baseline.plan) if baseline else {}
    new=_cells(snapshot.plan)
    actual={(r.date,r.line_code,r.model_code):r.quantity for r in snapshot.execution.facts}
    today=production_date(snapshot.time.as_of)
    def execution_known(key):
        return key in actual or key[0]>today or _covered(key,snapshot.execution.coverage)
    relevant=defaultdict(set)
    for key in old.keys()|new.keys():
        if snapshot.time.window_start<=key[0]<=snapshot.time.window_end:
            relevant[(key[0]-snapshot.time.window_start).days//7].add(key)
    # Declared coverage establishes zero only for the named dates/lines/models.
    # It never expands to all masters, all fifteen weeks, or unrelated blanks.
    declared=defaultdict(set)
    if baseline:
        for coverage in baseline.coverage:
            if not coverage.blanks_are_zero: continue
            day=max(coverage.start_date,snapshot.time.window_start)
            while day<=min(coverage.end_date,snapshot.time.window_end):
                number=(day-snapshot.time.window_start).days//7
                for line in coverage.line_codes:
                    for model in coverage.model_codes: declared[number].add((day,line,model))
                day+=timedelta(days=1)
    weeks=[]; cost=Fraction(0); complete=True
    for number in range(15):
        week=snapshot.time.window_start+timedelta(days=number*7)
        band='near' if number<prefs.near_weeks else 'far'
        tolerance=getattr(prefs,band+'_tolerance_percent')
        weight=Fraction(getattr(prefs,band+'_week_weight'))
        old_total=new_total=change=unknown_base=unknown_execution=0
        known_keys=set()
        for key in relevant[number]:
            if not baseline or (key not in old and key not in declared[number]):
                unknown_base+=1; continue
            known_keys.add(key)
            if not execution_known(key):
                unknown_execution+=1; continue
            p=max(0,old.get(key,0)-actual.get(key,0))
            n=max(0,new.get(key,0)-actual.get(key,0))
            old_total+=p;new_total+=n;change+=abs(n-p)
        known_baseline=bool(known_keys or declared[number])
        # A partial dated declaration does not establish the rest of its week.
        declared_pairs={(line,model) for _,line,model in declared[number]}
        baseline_complete=known_baseline and not unknown_base and bool(declared_pairs) and all(
            all((week+timedelta(days=d),line,model) in declared[number] for d in range(7))
            for line,model in declared_pairs)
        rate=Fraction(change,old_total) if old_total else Fraction(0) if known_baseline and not new_total else None
        state='compared'
        if not known_baseline: state='new_plan';rate=None
        elif unknown_execution: state='unknown_execution';rate=None
        elif not baseline_complete: state='partial'
        elif not old_total and new_total: state='zero_baseline'
        if state=='new_plan': display='新排计划'
        elif state=='unknown_execution': display='执行事实未确认，变化率未完成'
        elif rate is None: display=f'原计划为零，新增 {new_total} 片'
        else: display=_percent(rate)
        if state=='partial': display='覆盖不完整，已知范围'+display
        exceeds=bool(rate is not None and rate*100>Fraction(tolerance))
        cost+=change*weight
        complete=complete and not unknown_execution and not unknown_base and (not known_baseline or baseline_complete)
        weeks.append(StabilityWeek(week=week,number=number+1,band=band,state=state,
            old_remaining=old_total,new_remaining=new_total,absolute_change=change,added=new_total if not old_total else 0,
            rate=str(rate) if rate is not None else None,rate_display=display,tolerance_percent=tolerance,
            exceeds_tolerance=exceeds,baseline_complete=baseline_complete,unknown_baseline_cells=unknown_base,unknown_execution_cells=unknown_execution))
    order=0
    if baseline:
        old_sequence=_remaining_segments(baseline.plan,actual,execution_known)
        new_sequence=_remaining_segments(snapshot.plan,actual,execution_known)
        old_sequence={k:v for k,v in old_sequence.items() if snapshot.time.window_start<=k[0]<=snapshot.time.window_end}
        order=_order_changes(old_sequence,new_sequence)
    return StabilityResult(baseline_version=baseline.version if baseline else None,weeks=weeks,
        weighted_cost=str(cost),order_change_count=order,comparison_complete=complete)
