"""Fixed 18-line, 40-model, 105-day example with independently known totals."""
from datetime import date, datetime, timedelta

START = date(2026, 10, 12)
NOW = datetime.fromisoformat('2026-10-12T08:00:00+08:00')


def full_window():
    models = [f'M{i:02d}' for i in range(40)]
    recipes = [f'R{i:02d}' for i in range(20)]
    lines = [f'L{i:02d}' for i in range(18)]
    days = [START + timedelta(days=i) for i in range(105)]
    months = sorted({day.strftime('%Y-%m') for day in days})
    segments, switches, state = [], [], dict.fromkeys(lines)
    for day in days:
        for li, line in enumerate(lines):
            order = 0
            for mi in range(li, 40, 18):
                recipe = recipes[mi // 2]
                if state[line] is not None and state[line] != recipe:
                    switches.append({'id': f'X{len(switches):05d}', 'line_code': line,
                        'from_recipe': state[line], 'to_recipe': recipe,
                        'allocations': [{'date': day.isoformat(), 'order': order, 'hours': '0', 'idle_hours': '0'}]})
                    order += 1
                segments.append({'id': f'S{len(segments):05d}', 'date': day.isoformat(),
                    'line_code': line, 'order': order, 'model_code': models[mi], 'quantity': 100, 'hours': '1'})
                order += 1
                state[line] = recipe
    return {'time': {'as_of': NOW.isoformat(), 'window_start': START.isoformat(), 'weeks': 15},
        'inputs': {'schema_version': 1, 'input_version': 1,
            'masters': {
                'models': [{'code': model, 'name': model, 'recipe_code': recipes[i // 2], 'active': True} for i, model in enumerate(models)],
                'recipes': [{'code': r, 'name': r, 'active': True} for r in recipes],
                'lines': [{'code': line, 'name': line, 'active': True} for line in lines],
                'capabilities': [{'recipe_code': r, 'line_code': line, 'allowed': True} for r in recipes for line in lines]},
            'parameters': {
                'line_yields': [{'month': m, 'percent': '100'} for m in months],
                'model_yields': [{'month': m, 'model_code': model, 'percent': '100'} for m in months for model in models],
                'capacities': [{'month': m, 'recipe_code': r, 'line_code': line, 'pieces': 2400} for m in months for r in recipes for line in lines],
                'cycle_times': [{'model_code': model, 'days': '0'} for model in models],
                'switch_durations': [{'from_recipe': a, 'to_recipe': b, 'hours': '0'} for a in recipes for b in recipes if a != b]},
            'calendar': {'holidays': [], 'opening_exceptions': [], 'maintenance': [], 'experiments': []},
            'business': {'demands': [{'model_code': model, 'week': (START + timedelta(weeks=w)).isoformat(), 'quantity': 700} for w in range(15) for model in models], 'confirmations': []}},
        'plan': {'segments': segments, 'switches': switches, 'initial_states': [{'line_code': line, 'recipe_code': None} for line in lines], 'ordinary_idle': []},
        'execution': {'facts': [], 'cutoff': NOW.isoformat(), 'coverage': [{'start_date': START.isoformat(), 'end_date': START.isoformat(), 'line_codes': lines, 'model_codes': models, 'blanks_are_zero': True}]},
        'locks': [], 'baseline': None, 'reset': None}


def seeded_app(payload):
    """Only persistence, authentication and clock are replaced at their boundaries."""
    from auto_plan.app import create_app
    from auto_plan.input_models import InputSnapshot
    from auto_plan.plan_models import Plan
    from auto_plan.plans import PlanningContext
    from auto_plan.settings import Settings
    from tests.input_support import MemoryInputStore
    from tests.plan_support import MemoryPlanStore
    from tests.support import CompanyAuthentication, MemoryAccessStore
    inputs = MemoryInputStore()
    inputs.versions[1] = InputSnapshot.model_validate(payload['inputs'])
    inputs.version = 1
    plans = MemoryPlanStore(inputs)
    plans.plan = Plan.model_validate(payload['plan'])
    plans.context = PlanningContext(execution=payload['execution'])
    plans.revision = 1
    plans.revisions[1] = {'snapshot': payload, 'coverage': [], 'lineage': {}, 'operations': [], 'effects': []}
    access = MemoryAccessStore()
    access.users['SCALEEDITOR'] = {'user_id': 'SCALEEDITOR', 'roles': ['editor'], 'active': True}
    return create_app(Settings(public_origin='https://testserver'), store=access, input_store=inputs,
        plan_store=plans, authenticator=CompanyAuthentication(), clock=lambda: NOW)


def final_day_command(payload):
    last = '2027-01-24'
    changed = [row | {'quantity': 99} for row in payload['plan']['segments'] if row['date'] == last]
    assert len(changed) == 40
    return {'base_revision': 1, 'input_version': 1, 'latest_adoption': 0,
        'operations': [{'kind': 'patch_plan', 'segments': changed}]}


def check_final_day(result):
    assert result['capacity_valid'] and result['computation_complete'] and result['executable']
    assert result['issues'] == []
    totals = result['output_totals']
    assert totals['expected'] == totals['on_time'] == 419960
    assert totals['gap'] == 40
    assert all(totals[key] == 0 for key in ('late', 'pending', 'unallocated', 'outside_window'))
    assert len(result['weekly_results']) == 600
    for row in result['weekly_results']:
        final = row['week'] == '2027-01-18'
        assert row['on_time'] == (699 if final else 700)
        assert row['gap'] == (1 if final else 0)


def check_full_window(result):
    assert result['capacity_valid'] and result['computation_complete'] and result['executable']
    assert result['issues'] == []
    assert result['switch_count'] == 4182
    assert len(result['output_sources']) == len(result['attributions']) == 4200
    assert len(result['days']) == 1890
    totals = result['output_totals']
    assert totals['expected'] == totals['on_time'] == 420000
    assert all(totals[key] == 0 for key in ('gap', 'late', 'pending', 'unallocated', 'outside_window'))
    assert len(result['weekly_results']) == 600
    assert all(row['on_time'] == 700 and row['gap'] == 0 for row in result['weekly_results'])
