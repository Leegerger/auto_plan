"""Freeze independently constructed acceptance witnesses. Uses only the standard library."""
import argparse
from copy import deepcopy
from datetime import date, timedelta
from fractions import Fraction
import gzip
import hashlib
import json
from pathlib import Path

START = date(2026, 10, 12)
LINES = [f'L{i:02d}' for i in range(18)]
MODELS = [f'M{i:02d}' for i in range(40)]
MONTHS = ['2026-10', '2026-11', '2026-12', '2027-01', '2027-02']
GATES = {'selection': 1, 'single_cell': 1, 'week': 3, 'day': 3, 'batch_preview': 5,
         'import_validation': 5, 'shared_save': 5, 'local': 30, 'new_week': 120, 'all_replan': 120}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def coverage(start=START, end=None):
    return {'start_date': start.isoformat(), 'end_date': (end or start + timedelta(days=104)).isoformat(),
            'line_codes': LINES, 'model_codes': MODELS, 'blanks_are_zero': True}


def loose(count, key_lines=None, rolling=False):
    recipes = [f'R{i:02d}' for i in range(count)]
    group = 40 // count
    model_recipe = {model: recipes[i // group] for i, model in enumerate(MODELS)}
    home = {r: (18 * i // 10 if count == 10 else i % 18) for i, r in enumerate(recipes)}
    placement = {}
    for i, model in enumerate(MODELS):
        recipe = model_recipe[model]
        line = home[recipe] + (int(i % 4 >= 2) if count == 10 else 0)
        placement[model] = LINES[0 if key_lines == 1 and recipe == 'R00' else line]
    by_line = {line: sorted([m for m in MODELS if placement[m] == line], key=lambda m: (model_recipe[m], m)) for line in LINES}
    initial = {line: model_recipe[models[-1 if rolling else 0]] for line, models in by_line.items()}
    start = START + timedelta(days=7 if rolling else 0)
    segments, switches, state = [], [], initial.copy()
    for offset in range(105):
        day = start + timedelta(days=offset)
        for line, models in by_line.items():
            order = 0
            for model in models:
                recipe = model_recipe[model]
                if state[line] != recipe:
                    duration = '1' if state[line] < recipe else '2'
                    switches.append({'id': f'X-{day}-{line}-{recipe}', 'line_code': line,
                        'from_recipe': state[line], 'to_recipe': recipe,
                        'allocations': [{'date': day.isoformat(), 'order': order, 'hours': duration, 'idle_hours': '0'}]})
                    order += 1
                segments.append({'id': f'S-{day}-{model}', 'date': day.isoformat(), 'line_code': line,
                                 'order': order, 'model_code': model, 'quantity': 100, 'hours': '1'})
                order += 1
                state[line] = recipe
    plan = {'segments': segments, 'switches': switches,
            'initial_states': [{'line_code': line, 'recipe_code': initial[line]} for line in LINES], 'ordinary_idle': []}
    snapshot = {'time': {'as_of': start.isoformat() + 'T08:00:00+08:00', 'window_start': start.isoformat(), 'weeks': 15},
        'inputs': {'schema_version': 1, 'input_version': 1,
            'masters': {'models': [{'code': m, 'name': m, 'recipe_code': model_recipe[m], 'active': True} for m in MODELS],
                        'recipes': [{'code': r, 'name': r, 'active': True} for r in recipes],
                        'lines': [{'code': line, 'name': line, 'active': True} for line in LINES],
                        'capabilities': [{'recipe_code': r, 'line_code': line, 'allowed': (i < key_lines if key_lines and r == 'R00' else (i-home[r]) % 18 < 3+ri % 4)} for ri, r in enumerate(recipes) for i, line in enumerate(LINES)]},
            'parameters': {'line_yields': [{'month': month, 'percent': '100'} for month in MONTHS],
                'model_yields': [{'month': month, 'model_code': m, 'percent': '100'} for month in MONTHS for m in MODELS],
                'capacities': [{'month': month, 'recipe_code': r, 'line_code': line, 'pieces': 2400} for month in MONTHS for r in recipes for line in LINES],
                'cycle_times': [{'model_code': m, 'days': '0'} for m in MODELS],
                'switch_durations': [{'from_recipe': a, 'to_recipe': b, 'hours': '1' if a < b else '2'} for a in recipes for b in recipes if a != b]},
            'calendar': {'holidays': [], 'opening_exceptions': [], 'maintenance': [], 'experiments': []},
            'business': {'demands': [{'model_code': m, 'week': (start + timedelta(weeks=w)).isoformat(), 'quantity': 700} for w in range(15) for m in MODELS], 'confirmations': []}},
        'plan': plan, 'execution': {'facts': [], 'coverage': [coverage(start)], 'cutoff': start.isoformat() + 'T08:00:00+08:00'},
        'locks': [], 'reset': None,
        'baseline': {'version': 1, 'plan': deepcopy(plan), 'coverage': [coverage(start)]}}
    if rolling:
        old = loose(count)
        snapshot['baseline'] = old['baseline']
        snapshot['inputs']['business']['demands'] = old['inputs']['business']['demands'] + [
            {'model_code': m, 'week': '2027-01-25', 'quantity': 700} for m in MODELS]
    return snapshot


def totals(quantity=420000, gap=0, pending=0, late=0, on_time=None):
    return {'expected': quantity, 'on_time': quantity if on_time is None else on_time, 'late': late,
            'gap': gap, 'pending': pending, 'unallocated': 0, 'outside_window': 0}


def objective(switches, cost=0, gap=0):
    return ['0'] * 14 + [str(gap)] + ['0'] * 14 + [str(gap), str(switches), str(cost), '0']


def boundary(count):
    snapshot = loose(count)
    recipe = {r['code']: r['recipe_code'] for r in snapshot['inputs']['masters']['models']}
    grouped = {}
    for row in snapshot['plan']['segments']:
        grouped.setdefault((row['date'], row['line_code']), []).append(row)
    switch_hours = {}
    for switch in snapshot['plan']['switches']:
        for allocation in switch['allocations']:
            key = allocation['date'], switch['line_code']
            switch_hours[key] = switch_hours.get(key, 0) + int(allocation['hours'])
    for (day, line), rows in grouped.items():
        by_recipe = {}
        for row in rows:
            by_recipe.setdefault(recipe[row['model_code']], []).append(row)
        memberships = len(by_recipe)
        rows[0]['quantity'] += memberships - 1
        for members in by_recipe.values():
            quantity = sum(row['quantity'] for row in members)
            hours = Fraction(quantity - 1, 100) + Fraction(1, 100 * memberships)
            members[0]['hours'] = str(hours - (len(members) - 1))
        snapshot['inputs']['calendar']['maintenance'].append({'date': day, 'line_code': line,
            'hours': 24 - len(rows) - switch_hours.get((day, line), 0)})
    daily = {row['model_code']: row['quantity'] for row in snapshot['plan']['segments'] if row['date'] == START.isoformat()}
    for demand in snapshot['inputs']['business']['demands']:
        demand['quantity'] = daily[demand['model_code']] * 7
    snapshot['baseline']['plan'] = deepcopy(snapshot['plan'])
    return snapshot


def initial_import(snapshot):
    """Source-only public import schema. Exact witnesses are installed separately."""
    start = snapshot['time']['window_start']
    changes = {kind: rows for section in ('masters', 'parameters', 'calendar')
               for kind, rows in snapshot['inputs'][section].items()}
    changes['confirmations'] = snapshot['inputs']['business']['confirmations']
    return {'base_revision': 0, 'input_version': 0, 'latest_adoption': 0, 'changes': changes,
        'segments': [{key: value for key, value in row.items() if key != 'hours'} for row in snapshot['plan']['segments']],
        'demands': snapshot['inputs']['business']['demands'], 'complete_weekly_demands': True,
        'coverage': snapshot['baseline']['coverage'], 'initial_states': snapshot['plan']['initial_states'],
        'execution': {key: snapshot['execution'][key] for key in ('facts', 'coverage', 'cutoff')},
        'reset': snapshot.get('reset') or {'batch_id': 'synthetic-zero-wip', 'production_week': start,
                  'cutoff': start + 'T08:00:00+08:00',
                  'models': [{'model_code': row['code'], 'wip': 0, 'net_balance': 0}
                             for row in snapshot['inputs']['masters']['models']]}}


def combined(case, allowed=False):
    start = date(2026, 10, 26)
    snapshot = loose(20)
    snapshot['time'] = {'as_of': ('2026-10-30' if case == 'a' else '2026-11-01') + 'T12:00:00+08:00', 'window_start': start.isoformat(), 'weeks': 15}
    masters = snapshot['inputs']['masters']
    masters['models'] = [masters['models'][0]]
    masters['recipes'] = [masters['recipes'][0]]
    masters['lines'] = masters['lines'][:3]
    masters['capabilities'] = [{'recipe_code': 'R00', 'line_code': line, 'allowed': True} for line in LINES[:3]]
    parameters = snapshot['inputs']['parameters']
    parameters['model_yields'] = [row | {'percent': '98' if row['month'] == '2026-10' else '100'} for row in parameters['model_yields'] if row['model_code'] == 'M00']
    parameters['line_yields'] = [row | {'percent': '95' if row['month'] == '2026-10' else '100'} for row in parameters['line_yields']]
    parameters['capacities'] = [row | {'pieces': 24000 if case == 'a' else 2400} for row in parameters['capacities'] if row['recipe_code'] == 'R00' and row['line_code'] in LINES[:3]]
    parameters['cycle_times'] = [{'model_code': 'M00', 'days': '0.5'}]
    parameters['switch_durations'] = []
    calendar = snapshot['inputs']['calendar']
    calendar['holidays'] = [{'date': day, 'holiday': True} for day in (['2026-10-31', '2026-11-01', '2026-11-02'] if case == 'a' else ['2026-11-01', '2026-11-02'])]
    day = '2026-10-30' if case == 'a' else '2026-11-01'
    plan = {'segments': [{'id': 'combined-L00', 'date': day, 'line_code': 'L00', 'model_code': 'M00', 'quantity': 800 if case == 'a' else 1, 'hours': '4/5' if case == 'a' else '1/100', 'order': 0}],
            'switches': [], 'initial_states': [{'line_code': line, 'recipe_code': 'R00'} for line in LINES[:3]], 'ordinary_idle': []}
    if case == 'a':
        plan['segments'].append({'id': 'combined-L01', 'date': day, 'line_code': 'L01', 'model_code': 'M00', 'quantity': 272, 'hours': '34/125', 'order': 0})
    else:
        parameters['model_yields'] = [row | {'percent': '50'} for row in parameters['model_yields']]
        parameters['line_yields'] = [row | {'percent': '100'} for row in parameters['line_yields']]
        calendar['opening_exceptions'] = [{'date': day, 'line_code': 'L00', 'enabled': True}]
    cover = {'start_date': start.isoformat(), 'end_date': (start + timedelta(days=104)).isoformat(), 'line_codes': LINES[:3], 'model_codes': ['M00'], 'blanks_are_zero': True}
    fact = {'date': day, 'line_code': 'L00', 'model_code': 'M00', 'quantity': 600 if case == 'a' else 1}
    snapshot['plan'] = plan
    snapshot['execution'] = {'facts': [fact], 'coverage': [cover], 'cutoff': snapshot['time']['as_of'],
                             'associations': [{'segment_id': 'combined-L00', 'quantity': fact['quantity']}], 'association_cells': [fact]}
    snapshot['locks'] = [{'date': day, 'line_code': 'L00', 'model_code': 'M00', 'quantity': 800 if case == 'a' else 1},
                         {'date': day, 'line_code': 'L02' if case == 'a' else 'L01', 'model_code': 'M00', 'quantity': 0}]
    snapshot['baseline'] = {'version': 1, 'plan': deepcopy(plan), 'coverage': [cover]}
    snapshot['reset'] = {'batch_id': 'combined-zero' if case == 'a' else 'combined-wip', 'production_week': start.isoformat(), 'cutoff': start.isoformat() + 'T08:00:00+08:00', 'models': [{'model_code': 'M00', 'wip': 0 if case == 'a' else 3, 'net_balance': 0}]}
    demands = [{'model_code': 'M00', 'week': (start + timedelta(weeks=w)).isoformat(), 'quantity': (600 if case == 'a' else 3) if w == 0 else (400 if case == 'a' else 1) if w == 1 else 0} for w in range(15)]
    confirmations = [{'model_code': 'M00', 'week': start.isoformat(), 'kind': 'shipping', 'batch_id': 'late', 'status': 'allowed', 'date': '2026-11-03', 'quantity': 100 if case == 'a' else 1}]
    if case == 'a':
        confirmations.append({'model_code': 'M00', 'week': '2026-11-02', 'kind': 'material', 'batch_id': 'partial-material', 'status': 'allowed', 'date': day, 'quantity': 429 if allowed else 428})
    elif not allowed:
        confirmations.append({'model_code': 'M00', 'week': '2026-11-02', 'kind': 'material', 'batch_id': 'whole-week-ban', 'status': 'forbidden'})
    snapshot['inputs']['business'] = {'demands': demands, 'confirmations': confirmations}
    return snapshot


def write_assets(output):
    output.mkdir(parents=True, exist_ok=False)
    manifest = {'schema_version': 1, 'prepared_at': '2026-10-15',
        'reference_confirmation': {'status': 'not_run', 'reason': 'User has not confirmed these serialized distributions, actions or reference vectors.'},
        'gates_seconds': GATES, 'repeats_per_mode': 5, 'include_first_repeat': True,
        'load_modes': ['solo', 'five_automated_editors_one_computation'], 'scenarios': []}

    def freeze(value):
        data = canonical(value)
        sha = hashlib.sha256(data).hexdigest()
        path = sha + '.json.gz'
        (output / path).write_bytes(gzip.compress(data, mtime=0))
        return {'path': path, 'sha256': sha, 'canonical_bytes': len(data)}

    def add(ident, kind, source, reference, expected_totals, vector, scope=None, notes=None):
        start = source['time']['window_start']
        end = (date.fromisoformat(start) + timedelta(days=104)).isoformat()
        scope = scope or {'start_date': start, 'end_date': end, 'line_codes': LINES, 'model_codes': MODELS}
        action = {'base_revision': 1, 'input_version': 1, 'latest_adoption': 1,
                  'request': {'mode': 'all_replan' if kind in ('all_replan', 'loose', 'boundary', 'key1', 'key2', 'cross_day') else 'new_week' if kind == 'rolling' else 'local',
                              'scope': scope, 'strategy': 'hybrid', 'time_limit_seconds': 20 if kind.startswith('local') or kind == 'partial_gap' else 90,
                              'max_candidates': 1, 'native_threads': 1}}
        manifest['scenarios'].append({'id': ident, 'kind': kind,
            'source': freeze(source), 'reference': freeze(reference), 'action': freeze(action),
            'import': freeze(initial_import(source)), 'scope': scope,
            'tokens': {'source_revision': 1, 'input_version': 1, 'latest_adoption': 1,
                       'binding': 'Logical frozen tokens. Live bindings must be recorded after public preparation; do not silently rewrite a measured run.'},
            'expected': {'totals': expected_totals, 'objective': vector, 'capacity_valid': True,
                         'computation_complete': True, 'executable': True},
            'reference_kind': 'independent feasible witness; upper bound, not global optimality proof',
            'quality_acceptance': 'not_run', 'notes': notes or []})

    for count in (10, 20, 40):
        base = loose(count)
        switches = 4182 if count == 40 else 418
        add(f'r{count}-loose', 'loose', base, base, totals(), objective(switches))
        add(f'r{count}-all-replan', 'all_replan', base, base, totals(), objective(switches))
        for key_lines in (1, 2):
            key = loose(count, key_lines)
            s = 209 if count == 10 and key_lines == 1 else switches
            add(f'r{count}-key{key_lines}', f'key{key_lines}', key, key, totals(), objective(s))
        tight = boundary(count)
        quantity = 422310 if count == 40 else 420210
        add(f'r{count}-boundary', 'boundary', tight, tight, totals(quantity), objective(switches),
            notes=['Maximum pieces only for fixed Recipe membership, switches and production budget. Severe maintenance is synthetic.'])
        cross = deepcopy(base)
        switch = next(r for r in cross['plan']['switches'] if r['allocations'][0]['hours'] == '2')
        target = switch['allocations'][0]
        prior = (date.fromisoformat(target['date']) - timedelta(days=1)).isoformat()
        last_order = max(r['order'] for r in cross['plan']['segments'] if r['date'] == prior and r['line_code'] == switch['line_code'])
        target['hours'] = '1'
        switch['allocations'].insert(0, {'date': prior, 'order': last_order + 1, 'hours': '1', 'idle_hours': '0'})
        cross['baseline']['plan'] = deepcopy(cross['plan'])
        add(f'r{count}-cross-day', 'cross_day', cross, cross, totals(), objective(switches))
        for band, week, day, weight in [('near', '2026-10-19', '2026-10-25', 3), ('far', '2026-11-30', '2026-12-06', 1)]:
            for name, delta in [('increase', 40), ('decrease', -40)]:
                source = deepcopy(base)
                next(r for r in source['inputs']['business']['demands'] if r['model_code'] == 'M00' and r['week'] == week)['quantity'] += delta
                reference = deepcopy(source)
                row = next(r for r in reference['plan']['segments'] if r['model_code'] == 'M00' and r['date'] == day)
                row.update(quantity=100 + delta, hours=str(Fraction(100 + delta, 100)))
                scope = {'start_date': day, 'end_date': day, 'line_codes': ['L00'], 'model_codes': ['M00']}
                add(f'r{count}-{band}-{name}', 'local_' + name, source, reference, totals(420000 + delta), objective(switches, 40 * weight), scope,
                    ['Only this demand week changes. Entire-plan weekly absolute change rate is exactly 1/700.'])
        rolled = loose(count, rolling=True)
        source = deepcopy(rolled)
        source['plan']['segments'] = [r for r in source['plan']['segments'] if r['date'] < '2027-01-25']
        source['plan']['switches'] = [r for r in source['plan']['switches'] if r['allocations'][-1]['date'] < '2027-01-25']
        add(f'r{count}-rolling', 'rolling', source, rolled, totals(), objective(4200 if count == 40 else 420),
            {'start_date': '2027-01-25', 'end_date': '2027-01-31', 'line_codes': LINES, 'model_codes': MODELS},
            ['Actual rolling window starts 2026-10-19. Prior demand history is retained and excluded explicitly.',
             'Old baseline coverage stops 2027-01-24. New week cost is zero; it is not an old zero baseline.',
             'External shared run requires an authorized environment at the frozen production week; no runtime clock bypass is supplied.'])
    partial = boundary(20)
    next(r for r in partial['inputs']['business']['demands'] if r['model_code'] == 'M00' and r['week'] == '2027-01-18')['quantity'] += 40
    add('r20-partial-gap', 'partial_gap', partial, partial, totals(420210, gap=40), objective(418, gap=40),
        {'start_date': '2027-01-24', 'end_date': '2027-01-24', 'line_codes': ['L00'], 'model_codes': ['M00']},
        ['Known 40-piece shortage under this fixed membership and frozen outside plan. This is not a typical feasible timing success.'])
    for case in ('a', 'b'):
        for allowed in (False, True):
            snapshot = combined(case, allowed)
            ident = f"combined-{case}-{'confirmed' if allowed else 'partial' if case == 'a' else 'ban'}"
            expected = totals(1000 if case == 'a' else 4, gap=0 if allowed else 1, pending=0 if allowed else 1,
                              late=100 if case == 'a' else 1, on_time=(900 if allowed else 899) if case == 'a' else (3 if allowed else 2))
            vector = ['100' if case == 'a' else '1', '0' if allowed else '1'] + ['0'] * 13 + ['0', '0' if allowed else '1'] + ['0'] * 13 + ['0', '0', '0']
            add(ident, 'combined', snapshot, snapshot, expected, vector,
                {'start_date': snapshot['plan']['segments'][0]['date'], 'end_date': snapshot['plan']['segments'][0]['date'], 'line_codes': LINES[:3], 'model_codes': ['M00']},
                ['Small hand-derived rule companion, not full-scale distribution or timing evidence.', 'Facts, associations, WIP, positive and zero locks are explicit. No automatic current-day edit acknowledgment is supplied.'])
            manifest['scenarios'][-1]['expected']['executable'] = allowed
            manifest['scenarios'][-1]['reference_kind'] = 'independent exact arithmetic companion'
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New directory; existing evidence is never overwritten.')
    write_assets(parser.parse_args().output)
