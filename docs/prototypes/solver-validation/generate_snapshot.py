"""THROWAWAY synthetic review fixture. Not a solver or full validator.

Makes a concrete input/plan and output ledger for human review. Demand is
derived from this plan; it does not represent an independently optimized
or business-approved reference. All decimals are exact rational strings.
"""
from collections import defaultdict
from datetime import date, timedelta
from fractions import Fraction as F
from pathlib import Path
import hashlib
import json
import sys

START = date(2026, 10, 12)
DAYS = [START + timedelta(days=i) for i in range(105)]
HOLIDAYS = {d for d in DAYS if d.weekday() >= 5}
# Extend calendar for all tail outputs, rather than truncate to the window.
HOLIDAYS.update(START + timedelta(days=i) for i in range(105, 140) if (START + timedelta(days=i)).weekday() >= 5)
MONTHS = sorted({d.strftime('%Y-%m') for d in DAYS})


def ceilq(v):
    return -((-v.numerator) // v.denominator)


def outputs(day, quantity, factor, ct):
    n, frac = ct.numerator // ct.denominator, ct % 1
    calendar = [day]
    cursor = day
    while len(calendar) <= n + 1:
        cursor += timedelta(days=1)
        if cursor not in HOLIDAYS:
            calendar.append(cursor)
    slots = []
    for slot, share in [(n, 1 - frac), (n + 1, frac)]:
        if not share:
            continue
        target = calendar[slot]
        while target in HOLIDAYS:
            target += timedelta(days=1)
        slots.append({'slot': slot, 'date': target.isoformat(), 'quantity': ceilq(quantity * factor * share)})
    return slots


def week(day):
    return (day - START).days // 7


def recipe(model):
    return model // 2


def changeover(a, b):
    if a is None or a == b:
        return F(0)
    return F((3 * a + b) % 5, 2)


def main():
    root = Path(__file__).parent
    prefix = sys.argv[1] if len(sys.argv) > 1 else 'normal'
    if prefix not in ('normal', 'tail'):
        raise ValueError('choose normal or tail')
    lines = list(range(18))
    models = list(range(40))
    allowed = {r: sorted({(r + offset) % 18 for offset in range(3 + r % 4)}) for r in range(20)}
    capacity = {month: {f'{line}:{r}': 1800 + 120 * ((line + r + mi) % 11)
                        for r in range(20) for line in allowed[r]} for mi, month in enumerate(MONTHS)}
    line_yield = {month: str(F([99, 98, 97, 99][mi], 100)) for mi, month in enumerate(MONTHS)}
    model_yield = {month: {str(m): str(F(96 + (m + mi) % 5, 100)) for m in models}
                   for mi, month in enumerate(MONTHS)}
    ct = {str(m): str(F(m % 5, 2)) for m in models}
    plan, day_lines = [], []
    previous = {line: None for line in lines}
    for di, day in enumerate(DAYS):
        if day in HOLIDAYS:
            continue
        month = day.strftime('%Y-%m')
        for line in lines:
            available = 24 - (4 if (di + line) % 17 == 0 else 0)
            can = [r for r in range(20) if line in allowed[r]]
            r = can[(di + line) % len(can)]
            count = 1 + (di + line) % 3
            sequence = [2 * r]
            if count >= 2:
                sequence.append(2 * r + 1)
            if count == 3:
                other = can[(di + line + 1) % len(can)]
                sequence.append(2 * other + (di % 2))
            if prefix == 'normal':
                # Keep only sources whose complete output falls in the
                # window. The tail case retains the original construction.
                sequence = [m for m in sequence if all(
                    week(date.fromisoformat(slot['date'])) < 15
                    for slot in outputs(day, 1, F(1), F(ct[str(m)])))]
            if not sequence:
                continue
            transitions = []
            state = previous[line]
            for m in sequence:
                target = recipe(m)
                if state is not None and state != target:
                    transitions.append({'from': state, 'to': target, 'hours': str(changeover(state, target))})
                state = target
            previous[line] = state
            setup = sum((F(t['hours']) for t in transitions), F(0))
            # A nominal 70% production-time allocation, not a utilization proof.
            production = (F(available) - setup) * F(7, 10)
            hours = production / len(sequence)
            cell = {'date': day.isoformat(), 'line': line, 'available_hours': available,
                    'initial_recipe': transitions[0]['from'] if transitions else recipe(sequence[0]),
                    'transitions': transitions, 'production_hours_by_recipe': {},
                    'maintenance_hours': 24 - available}
            # Preserve the explicitly unknown initial state on the first day.
            if di == 0:
                cell['initial_recipe'] = None
            for position, m in enumerate(sequence):
                r = recipe(m)
                key = str(r)
                cell['production_hours_by_recipe'][key] = str(F(cell['production_hours_by_recipe'].get(key, '0')) + hours)
                amount = int(F(capacity[month][f'{line}:{r}']) * hours / 24)
                plan.append({'date': day.isoformat(), 'line': line, 'position': position, 'model': m, 'quantity': amount})
            day_lines.append(cell)

    sources = defaultdict(list)
    for row in plan:
        sources[(row['date'], row['model'])].append(row)
    # Remove any input unit that leaves every CT output slot unchanged.
    # This only handles per-source rounding tails, not full-plan surplus rules.
    for (day_string, m), rows in sources.items():
        day = date.fromisoformat(day_string)
        month = day.strftime('%Y-%m')
        factor = F(line_yield[month]) * F(model_yield[month][str(m)])
        total = sum(r['quantity'] for r in rows)
        target = outputs(day, total, factor, F(ct[str(m)]))
        while total and outputs(day, total - 1, factor, F(ct[str(m)])) == target:
            row = next(r for r in reversed(rows) if r['quantity'])
            row['quantity'] -= 1
            total -= 1

    demand = defaultdict(int)
    ledger = []
    for (day_string, m), rows in sorted(sources.items()):
        day = date.fromisoformat(day_string)
        month = day.strftime('%Y-%m')
        total = sum(r['quantity'] for r in rows)
        factor = F(line_yield[month]) * F(model_yield[month][str(m)])
        for slot in outputs(day, total, factor, F(ct[str(m)])):
            target_week = min(14, week(date.fromisoformat(slot['date'])))
            demand[(m, target_week)] += slot['quantity']
            ledger.append({'input_date': day_string, 'model': m, 'input_quantity': total,
                           'yield': str(factor), 'ct': ct[str(m)], **slot, 'demand_week': target_week})

    # All demand is constructed from chronological outputs in the same week
    # except the tail allocated to W15. That construction agrees with FIFO;
    # keep source identity and CT slots so an independent checker can verify it.
    ownership = defaultdict(lambda: defaultdict(int))
    for output in ledger:
        ownership[(output['input_date'], output['model'])][output['demand_week']] += output['quantity']
    material_need = defaultdict(int)
    shipping_need = defaultdict(int)
    allocations = []
    for key, portions in sorted(ownership.items()):
        source_date, m = key
        total = sum(row['quantity'] for row in sources[key])
        denominator = sum(portions.values())
        ideals = {w: F(total * y, denominator) for w, y in portions.items()}
        shares = {w: int(v) for w, v in ideals.items()}
        for w in sorted(shares, key=lambda w: (-(ideals[w] - shares[w]), w))[:total - sum(shares.values())]:
            shares[w] += 1
        for w in sorted(shares):
            allocations.append({'input_date': source_date, 'model': m, 'demand_week': w,
                                'attributed_input': shares[w], 'attributed_output': portions[w]})
            if week(date.fromisoformat(source_date)) < w:
                material_need[(m, w)] += shares[w]
    for output in ledger:
        if week(date.fromisoformat(output['date'])) > output['demand_week']:
            shipping_need[(output['model'], output['demand_week'])] += output['quantity']
    batches = []
    for kind, need in [('material', material_need), ('shipping', shipping_need)]:
        for (m, w), quantity in sorted(need.items()):
            # Two separately auditable batches. Dates coincide in the normal
            # reference; varied dates belong to the next targeted cases.
            for i, q in enumerate([quantity // 2, quantity - quantity // 2]):
                if q:
                    batches.append({'id': f'{kind}-{m:02}-{w:02}-{i}', 'type': kind, 'model': m,
                                    'demand_week': w, 'quantity': q, 'status': 'allowed',
                                    'date': START.isoformat() if kind == 'material' else max(o['date'] for o in ledger)})
    snapshot = {'status': 'synthetic_reference_pending_review_and_independent_validation', 'case': prefix,
                'start_date': START.isoformat(), 'production_day_start': '08:00 Asia/Shanghai',
                'days': 105, 'lines': lines, 'models': models,
                'model_recipe': {str(m): recipe(m) for m in models}, 'allowed_lines': {str(r): ls for r, ls in allowed.items()},
                'capacity_by_month': capacity, 'line_yield_by_month': line_yield,
                'model_yield_by_month': model_yield, 'ct': ct,
                'holidays': sorted(d.isoformat() for d in HOLIDAYS),
                'switch_hours': {f'{a}:{b}': str(changeover(a, b)) for a in range(20) for b in range(20)},
                'day_lines': day_lines, 'plan': plan,
                'baseline': 'same as reference plan; no executed facts or locks in normal reference',
                'demands': [{'model': m, 'week': w, 'quantity': q} for (m, w), q in sorted(demand.items())],
                'confirmation_batches': batches}
    canonical = json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode()
    digest = hashlib.sha256(canonical).hexdigest()
    (root/f'{prefix}-snapshot.json').write_text(json.dumps(snapshot, indent=2) + '\n')
    expected = {'input_sha256': digest, 'output_ledger': ledger, 'input_attribution': allocations}
    (root/f'{prefix}-expected.json').write_text(json.dumps(expected, indent=2) + '\n')
    weeks = []
    for w in range(15):
        required = sum(q for (m, wi), q in demand.items() if wi == w)
        late = sum(o['quantity'] for o in ledger if o['demand_week'] == w and week(date.fromisoformat(o['date'])) > w)
        weeks.append({'week': w + 1, 'demand': required, 'on_time': required-late, 'late': late})
    summary = {'input_sha256': digest, 'plan_segments': len(plan), 'sources': len(sources),
               'input_quantity': sum(r['quantity'] for r in plan), 'output_quantity': sum(o['quantity'] for o in ledger),
               'switches': sum(len(c['transitions']) for c in day_lines), 'confirmation_batches': len(batches),
               'weeks': weeks, 'status': snapshot['status']}
    (root/f'{prefix}-summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
