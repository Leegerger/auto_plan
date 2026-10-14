"""Exact deletion-only audit for ample-confirmation, all-on-time fixtures.

Not valid for general confirmation restrictions, locks, WIP or deadline
trade-offs. Quantities can only decrease; date/line/segment structure stays.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import date
from fractions import Fraction as F

from generate_snapshot import outputs


def trim(snapshot,candidate,mutable):
    plan = deepcopy(candidate['plan'])
    origin = date.fromisoformat(snapshot['start_date'])
    sources = defaultdict(list)
    for i,row in enumerate(plan):
        sources[(row['date'],row['model'])].append(i)
    need = defaultdict(int)
    for row in snapshot['demands']:
        need[(row['model'],row['week'])] += row['quantity']
    slack = {m:[-sum(need[(m,w)] for w in range(end+1)) for end in range(15)] for m in snapshot['models']}

    def cumulative(key,quantity):
        day,m = key
        factor = F(snapshot['line_yield_by_month'][day[:7]])*F(snapshot['model_yield_by_month'][day[:7]][str(m)])
        values = [0]*15
        for slot in outputs(date.fromisoformat(day),quantity,factor,F(snapshot['ct'][str(m)])):
            w = (date.fromisoformat(slot['date'])-origin).days//7
            if not 0<=w<15:
                raise ValueError('deletion audit requires all output inside window')
            for end in range(w,15):
                values[end] += slot['quantity']
        return values

    for key,indices in sources.items():
        current = cumulative(key,sum(plan[i]['quantity'] for i in indices))
        for w in range(15):
            slack[key[1]][w] += current[w]
    if any(value<0 for values in slack.values() for value in values):
        raise ValueError('deletion audit requires full on-time coverage')
    baseline = defaultdict(int)
    for row in snapshot['baseline_plan']:
        baseline[(row['date'],row['line'],row['model'])]+=row['quantity']
    removed = []
    zero_segment_review = []
    for key,indices in sorted(sources.items()):
        quantity = sum(plan[i]['quantity'] for i in indices)
        before = cumulative(key,quantity)
        eligible = [i for i in indices if i in mutable]
        removable = sum(plan[i]['quantity']-1 for i in eligible)
        def feasible(reduction):
            after = cumulative(key,quantity-reduction)
            return all(before[w]-after[w] <= slack[key[1]][w] for w in range(15))
        lo,hi = 0,removable
        while lo<hi:
            middle=(lo+hi+1)//2
            if feasible(middle):
                lo=middle
            else:
                hi=middle-1
        reduction=lo
        left=reduction
        # First undo added units, then remove from unchanged old production.
        for preserve_baseline in (True,False):
            for i in reversed(eligible):
                row=plan[i]
                old=baseline[(row['date'],row['line'],row['model'])]
                floor=max(1,old) if preserve_baseline else 1
                amount=min(left,max(0,row['quantity']-floor))
                row['quantity']-=amount
                left-=amount
        if left:
            raise RuntimeError('surplus removal allocation failed')
        after=cumulative(key,quantity-reduction)
        for w in range(15):
            slack[key[1]][w]-=before[w]-after[w]
        if reduction:
            removed.append({'input_date':key[0],'model':key[1],'quantity':reduction})
        # Deleting a last segment changes Recipe sequencing. Do not silently
        # treat that case as checked by this fixed-structure audit.
        new_q=quantity-reduction
        if any(plan[i]['quantity']==1 for i in eligible):
            last_delta=[a-b for a,b in zip(after,cumulative(key,new_q-1))]
            if all(last_delta[w]<=slack[key[1]][w] for w in range(15)):
                zero_segment_review.append({'input_date':key[0],'model':key[1]})
    return [row['quantity'] for row in plan],{
        'removed':removed,'total_removed':sum(row['quantity'] for row in removed),
        'zero_segment_review_required':zero_segment_review,
        'scope':__doc__}
