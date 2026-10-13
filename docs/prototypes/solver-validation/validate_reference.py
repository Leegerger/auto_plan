"""Independent, exact checker for the NORMAL synthetic reference only.

Does not import the generator or its expected ledger. This deliberately
rejects extensions it does not yet support; it is not the full validator.
"""
from collections import defaultdict
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path
from time import perf_counter
import hashlib
import json
import sys


def check(snapshot):
    start = date.fromisoformat(snapshot['start_date'])
    days = snapshot['days']
    holiday = set(snapshot['holidays'])
    if snapshot.get('executed') or snapshot.get('locks') or snapshot.get('wip') or snapshot.get('holiday_exceptions'):
        raise ValueError('normal checker does not support these extensions')
    F = Fraction
    ceil = lambda x: (x.numerator + x.denominator - 1) // x.denominator
    grouped = defaultdict(list)
    inputs = defaultdict(int)
    for segment in snapshot['plan']:
        day = date.fromisoformat(segment['date'])
        assert 0 <= (day-start).days < days, 'input outside window'
        q = segment['quantity']
        assert type(q) is int and q > 0, 'non-positive or non-integer segment'
        assert segment['model'] in snapshot['models'], 'unknown model'
        assert segment['line'] in snapshot['lines'], 'unknown line'
        grouped[(segment['date'], segment['line'])].append(segment)
        inputs[(segment['model'], segment['date'])] += q
    cells = {(cell['date'], cell['line']): cell for cell in snapshot['day_lines']}
    assert len(cells) == len(snapshot['day_lines']), 'duplicate date/line'
    assert set(cells) == set(grouped), 'unaccounted cell'
    last_recipe = {line: None for line in snapshot['lines']}
    switches = 0
    for key in sorted(cells):
        day_string, line = key
        cell = cells[key]
        assert day_string not in holiday, 'normal reference produces on Holiday'
        availability = max(0, 24-cell['maintenance_hours'])
        assert availability == cell['available_hours'], 'availability mismatch'
        assert cell['initial_recipe'] == last_recipe[line], 'initial Recipe mismatch'
        rows = sorted(grouped[key], key=lambda row: row['position'])
        assert [row['position'] for row in rows] == list(range(len(rows))), 'invalid sequence'
        quantity_by_recipe = defaultdict(int)
        transitions = []
        for row in rows:
            r = snapshot['model_recipe'][str(row['model'])]
            assert line in snapshot['allowed_lines'][str(r)], 'recipe not allowed'
            quantity_by_recipe[r] += row['quantity']
            old = last_recipe[line]
            if old is not None and old != r:
                transitions.append({'from': old, 'to': r, 'hours': snapshot['switch_hours'][f'{old}:{r}']})
            last_recipe[line] = r
        assert transitions == cell['transitions'], 'switch accounting mismatch'
        switches += len(transitions)
        production = cell['production_hours_by_recipe']
        assert {int(r) for r in production} == set(quantity_by_recipe), 'production time Recipe mismatch'
        used = sum((F(t['hours']) for t in transitions), F(0))
        for r, q in quantity_by_recipe.items():
            h = F(production[str(r)])
            assert h >= 0, 'negative production time'
            c = snapshot['capacity_by_month'][day_string[:7]][f'{line}:{r}']
            assert c >= 0 and q <= ceil(F(c) * h / 24), 'capacity exceeded'
            used += h
        assert used <= availability, 'day capacity exceeded'

    outputs = []
    for (model, input_string), quantity in sorted(inputs.items()):
        input_day = date.fromisoformat(input_string)
        month = input_string[:7]
        g = quantity * F(snapshot['line_yield_by_month'][month]) * F(snapshot['model_yield_by_month'][month][str(model)])
        ct = F(snapshot['ct'][str(model)])
        integer_days = ct.numerator // ct.denominator
        remainder = ct-integer_days
        for distance, part in ((integer_days, 1-remainder), (integer_days+1, remainder)):
            if part == 0:
                continue
            cursor = input_day
            remaining = distance
            while remaining:
                cursor += timedelta(days=1)
                if cursor.isoformat() not in holiday:
                    remaining -= 1
            while cursor.isoformat() in holiday:
                cursor += timedelta(days=1)
            outputs.append({'model': model, 'input_date': input_string, 'slot': distance,
                            'date': cursor.isoformat(), 'quantity': ceil(g*part)})
    needs = {(row['model'], row['week']): row['quantity'] for row in snapshot['demands']}
    assert len(needs) == len(snapshot['demands']), 'duplicate demand'
    remaining_needs = dict(needs)
    assigned = []
    surplus = 0
    for output in sorted(outputs, key=lambda o: (o['date'], o['input_date'], o['slot'], o['model'])):
        remaining = output['quantity']
        for w in range(15):
            key = (output['model'], w)
            covered = min(remaining, remaining_needs.get(key, 0))
            if covered:
                assigned.append({**output, 'quantity': covered, 'week': w})
                remaining_needs[key] -= covered
                remaining -= covered
        surplus += remaining
    portions = defaultdict(lambda: defaultdict(int))
    for output in assigned:
        portions[(output['model'], output['input_date'])][output['week']] += output['quantity']
    attribution = {}
    for key, per_week in portions.items():
        quantity = inputs[key]
        total_output = sum(per_week.values())
        shares = {w: quantity*y//total_output for w, y in per_week.items()}
        remainder_order = sorted(shares, key=lambda w: (-(quantity*per_week[w] % total_output), w))
        for w in remainder_order[:quantity-sum(shares.values())]:
            shares[w] += 1
        assert sum(shares.values()) == quantity
        for w, q in shares.items():
            attribution[(key[0], key[1], w)] = q

    batches = {row['id']: dict(row, remaining=row['quantity']) for row in snapshot['confirmation_batches']}
    assert len(batches) == len(snapshot['confirmation_batches']), 'duplicate confirmation ID'
    assert all(row['status']=='allowed' for row in batches.values()), 'normal reference expects allowed batches'

    def consume(kind, model, w, when, quantity):
        used = 0
        for batch in sorted(batches.values(), key=lambda b: (b['date'], b['id'])):
            if batch['type'] != kind or batch['model'] != model or batch['demand_week'] != w:
                continue
            if kind == 'material' and when < batch['date']:
                continue
            if kind == 'shipping' and when > batch['date']:
                continue
            amount = min(quantity-used, batch['remaining'])
            batch['remaining'] -= amount
            used += amount
            if used == quantity:
                break
        return used

    material_outputs = {}
    for model, source_day, w in sorted(attribution, key=lambda key: (key[1], key[0], key[2])):
        iw = attribution[(model, source_day, w)]
        yw = portions[(model, source_day)][w]
        early = (date.fromisoformat(source_day)-start).days//7 < w
        if early and iw:
            allowed = consume('material', model, w, source_day, iw)
            material_outputs[(model, source_day, w)] = yw*allowed//iw
        else:
            material_outputs[(model, source_day, w)] = yw
    on_time = defaultdict(int)
    late = defaultdict(int)
    pending_material = 0
    pending_shipping = 0
    for output in sorted(assigned, key=lambda o:(o['date'], o['input_date'], o['slot'], o['model'])):
        model, w = output['model'], output['week']
        key = (model, output['input_date'], w)
        material = min(output['quantity'], material_outputs[key])
        material_outputs[key] -= material
        pending_material += output['quantity']-material
        if (date.fromisoformat(output['date'])-start).days//7 <= w:
            on_time[w] += material
        else:
            allowed = consume('shipping', model, w, output['date'], material)
            late[w] += allowed
            pending_shipping += material-allowed
    weekly = []
    for w in range(15):
        need = sum(q for (m, wi), q in needs.items() if wi == w)
        weekly.append({'week': w+1, 'demand': need, 'on_time': on_time[w], 'late': late[w],
                       'final_shortage': need-on_time[w]-late[w]})
    return {'switches': switches, 'input_quantity': sum(inputs.values()),
            'output_quantity': sum(row['quantity'] for row in outputs), 'unallocated_output': surplus,
            'pending_material': pending_material, 'pending_shipping': pending_shipping,
            'weeks': weekly, 'confirmation_used_by_type': {
                kind: sum(b['quantity']-b['remaining'] for b in batches.values() if b['type']==kind)
                for kind in ('material', 'shipping')},
            'coverage_scope': 'normal reference only; no executed/WIP/locks/bans/cross-day switch/adjustment scope validation'}


if __name__ == '__main__':
    root = Path(__file__).parent
    prefix = sys.argv[1] if len(sys.argv) > 1 else 'normal'
    if prefix not in ('normal', 'tail'):
        raise ValueError('choose normal or tail')
    start = perf_counter()
    snapshot = json.loads((root/f'{prefix}-snapshot.json').read_text())
    result = check(snapshot)
    result['input_sha256'] = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    expected = json.loads((root/f'{prefix}-summary.json').read_text())
    result['matches_generated_summary'] = all(result[k] == expected[k] for k in ('switches', 'input_quantity', 'output_quantity', 'input_sha256')) and all(
        all(actual[k] == proposed[k] for k in ('week','demand','on_time','late'))
        for actual, proposed in zip(result['weeks'], expected['weeks']))
    result['load_validate_seconds'] = perf_counter()-start
    (root/f'{prefix}-validation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))
    raise SystemExit(int(not result['matches_generated_summary']))
