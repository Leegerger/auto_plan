"""Throwaway exact boundary oracles. Not yet a complete candidate validator.

Sources contain already computed, individually rounded output slots; the
full candidate evaluator must independently derive these from frozen inputs.
"""
from collections import defaultdict
from fractions import Fraction as F


def attribute_and_confirm(sources, demands, batches, bans=()):
    """FIFO output ownership, Hamilton input shares, then confirmations.

Dates are ISO production-day labels. Demand entries include start/end dates.
End is inclusive for the production date, before next Monday 08:00.
Input sources must already combine actual and remaining quantities by
model/input date. WIP has kind='wip' and no new material-input quota.
"""
    by_id = {s['id']:s for s in sources}
    if len(by_id) != len(sources):
        raise ValueError('duplicate source identity')
    keys = [(s['model'],s['date']) for s in sources if s.get('kind','input')=='input']
    if len(set(keys)) != len(keys):
        raise ValueError('input sources must be aggregated across lines and execution categories')
    need = {(d['model'],d['week']):dict(d,remaining=d['quantity']) for d in demands}
    if len(need) != len(demands):
        raise ValueError('duplicate demand')
    rows = []
    for source in sources:
        for slot,output in enumerate(source['outputs']):
            rows.append({'source':source['id'],'model':source['model'],'date':output['date'],
                         'quantity':output['quantity'],'slot':slot})
    def chronology(row):
        source = by_id[row['source']]
        return (row['date'],0 if source.get('kind')=='wip' else 1,source['date'],row['slot'],row['source'])
    assigned,unallocated = [],0
    for output in sorted(rows,key=chronology):
        left = output['quantity']
        for key,demand in sorted(need.items()):
            if key[0] != output['model']:
                continue
            quantity = min(left,demand['remaining'])
            if quantity:
                assigned.append(dict(output,week=key[1],quantity=quantity))
                demand['remaining'] -= quantity
                left -= quantity
        unallocated += left
    portions = defaultdict(lambda:defaultdict(int))
    for row in assigned:
        portions[row['source']][row['week']] += row['quantity']
    attributed = {}
    for source_id,values in portions.items():
        source = by_id[source_id]
        if source.get('kind') == 'wip':
            for week in values:
                attributed[(source_id,week)] = 0
            continue
        total = sum(values.values())
        q = source['input_quantity']
        shares = {w:q*y//total for w,y in values.items()}
        for w in sorted(shares,key=lambda w:(-(q*values[w]%total),w))[:q-sum(shares.values())]:
            shares[w] += 1
        for w,value in shares.items():
            attributed[(source_id,w)] = value
    quotas = {b['id']:dict(b,remaining=b['quantity']) for b in batches}
    if len(quotas) != len(batches):
        raise ValueError('duplicate batch')
    banned = set(tuple(b) for b in bans)
    matches = []

    def consume(kind,source,w,day,quantity):
        remaining = quantity
        for batch in sorted(quotas.values(),key=lambda b:(b['date'],b['id'])):
            if batch['type']!=kind or batch['model']!=source['model'] or batch['week']!=w:
                continue
            if kind=='material' and day<batch['date']:
                continue
            if kind=='shipping' and day>batch['date']:
                continue
            used = min(remaining,batch['remaining'])
            if used:
                batch['remaining'] -= used
                remaining -= used
                matches.append({'source':source['id'],'week':w,'type':kind,'batch':batch['id'],'quantity':used})
            if remaining==0:
                break
        return quantity-remaining

    material = {}
    violations = []
    for key in sorted(attributed,key=lambda key:(by_id[key[0]]['date'],by_id[key[0]]['model'],key[1])):
        source_id,w = key
        source = by_id[source_id]
        iw,yw = attributed[key],portions[source_id][w]
        early = source.get('kind','input')=='input' and source['date']<need[(source['model'],w)]['start']
        if early and (source['model'],w,'material') in banned:
            material[key] = 0
            violations.append({'source':source_id,'week':w,'type':'material_ban','quantity':yw})
        elif early and iw:
            allowed = consume('material',source,w,source['date'],iw)
            material[key] = yw*allowed//iw
        else:
            material[key] = yw
    metrics = {key:{'on_time':0,'late':0,'pending_material':0,'pending_shipping':0,'banned':0} for key in need}
    for row in sorted(assigned,key=chronology):
        source_id,w = row['source'],row['week']
        source = by_id[source_id]
        key = (source['model'],w)
        quantity = row['quantity']
        late = row['date']>need[key]['end']
        early = source.get('kind','input')=='input' and source['date']<need[key]['start']
        material_banned = early and (*key,'material') in banned
        shipping_banned = late and (*key,'shipping') in banned
        if material_banned or shipping_banned:
            metrics[key]['banned'] += quantity
            if shipping_banned:
                violations.append({'source':source_id,'week':w,'type':'shipping_ban','quantity':quantity})
            # Ban remains a hard invalidity even if another confirmation is missing.
            continue
        available = min(quantity,material[(source_id,w)])
        material[(source_id,w)] -= available
        metrics[key]['pending_material'] += quantity-available
        if late:
            confirmed = consume('shipping',source,w,row['date'],available)
            metrics[key]['late'] += confirmed
            metrics[key]['pending_shipping'] += available-confirmed
        else:
            metrics[key]['on_time'] += available
    for key,m in metrics.items():
        m['final_shortage'] = need[key]['quantity']-m['on_time']-m['late']
    return {'attribution':attributed,'assignments':assigned,'metrics':metrics,'unallocated':unallocated,
            'violations':violations,'matches':matches,
            'quota_used':{bid:b['quantity']-b['remaining'] for bid,b in quotas.items()}}


def check_switch_allocations(events, calendar, production_hours, durations, baseline_events=None, authorized=None):
    """Check a day-level certificate, without claiming minute-level continuity.

after/before are the immediate surrounding production anchors; their link
to real production sequence must be checked by the integrated evaluator.
Calendar maps (date,line) to available hours AFTER calendar downtime.
"""
    used = defaultdict(F)
    if len({e['id'] for e in events}) != len(events):
        raise ValueError('duplicate switch event identity')

    def outside_projection(items):
        result = []
        for event in items:
            allocations = event['allocations'] or [{'date':event['before']['date'],'hours':'0'}]
            for allocation in allocations:
                key = (allocation['date'],event['line'])
                if authorized is not None and key not in authorized:
                    result.append((key,event['from'],event['to'],F(allocation['hours'])))
        return result

    for event in events:
        if event['from'] is None or event['from']==event['to']:
            raise ValueError('unknown start or same Recipe is not a switch event')
        before,after = event['before'],event['after']
        if (after['date'],after['position']) >= (before['date'],before['position']):
            raise ValueError('switch anchors are reversed')
        expected = F(durations.get((event['from'],event['to']),'12'))
        actual = F(0)
        for allocation in event['allocations']:
            day,hours = allocation['date'],F(allocation['hours'])
            if hours<0 or not after['date'] <= day <= before['date']:
                raise ValueError('switch time outside its production anchors')
            key = (day,event['line'])
            if key not in calendar:
                raise ValueError('missing calendar day')
            used[key] += hours
            actual += hours
        if actual != expected:
            raise ValueError('switch duration not conserved exactly')
    for key in set(used) | set(production_hours):
        if F(production_hours.get(key,0))+used[key] > F(calendar.get(key,0)):
            raise ValueError('daily production and switches exceed available time')
    if authorized is not None and outside_projection(events)!=outside_projection(baseline_events or []):
        raise ValueError('outside switch work changed; expand authorized scope')
    return {'switches':len(events),'occupied_hours':dict(used)}


def check_quantity_protection(original_remaining, candidate_remaining, executed, locks, authorized):
    """Here both plan arguments mean REMAINING quantities, never total input.

Executed is a frozen cumulative-fact map, not an increment to import again.
Locks refer to total executed plus remaining quantities in the grid cell.
"""
    for key,value in candidate_remaining.items():
        if type(value) is not int or value<0:
            raise ValueError('remaining input must be a nonnegative integer')
    for key in set(original_remaining) | set(candidate_remaining):
        if key not in authorized and original_remaining.get(key,0)!=candidate_remaining.get(key,0):
            raise ValueError('quantity changed outside authorization')
    for key,total in locks.items():
        if executed.get(key,0)+candidate_remaining.get(key,0)!=total:
            raise ValueError('locked aggregate quantity changed')
    return {key:executed.get(key,0)+candidate_remaining.get(key,0)
            for key in set(executed) | set(candidate_remaining)}
