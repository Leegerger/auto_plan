from tests.test_plan_transactions import shared_client, command, post, row
from tests.test_manual_editing import preview, scope


def capacity(inputs, pieces=1200):
    from auto_plan.input_models import InputSnapshot
    data = inputs.read().model_dump(mode='json')
    for value in data['parameters']['capacities']:
        value['pieces'] = pieces
    inputs.versions[1] = InputSnapshot.model_validate(data)


def change(quantity, end='2026-10-17', **extra):
    return {'kind': 'change_quantity', 'action_id': 'quantity-chain',
            'segment_id': 'a', 'quantity': quantity, 'end_date': end,
            'scope': scope('2026-10-15', end), **extra}


def arranged(result):
    return [(r['date'], r['model_code'], r['quantity']) for r in result['plan']['segments']]


def test_current_quantity_has_priority_and_displaces_complete_ordered_chain(shared_client):
    client, _, inputs = shared_client
    capacity(inputs)
    initial = [row(quantity=600) | {'hours': '12'},
               row('b', 'N', quantity=600) | {'order': 1, 'hours': '12'},
               row('c', 'N', day='2026-10-16', quantity=1200) | {'hours': '24'}]
    assert post(client, 'save', command({'kind': 'patch_plan', 'segments': initial})).status_code == 200
    result = preview(client, change(1000), base=1)
    assert result.status_code == 200, result.text
    assert arranged(result.json()) == [('2026-10-15', 'M', 1000), ('2026-10-15', 'N', 200),
                                      ('2026-10-16', 'N', 400), ('2026-10-16', 'N', 800),
                                      ('2026-10-17', 'N', 400)]
    assert result.json()['unallocated'] == []
    assert result.json()['evaluation']['capacity_valid'] is True
    assert preview(client, change(1000), base=1).json()['plan'] == result.json()['plan']


def test_chain_preserves_executed_pieces_and_locked_days_and_retains_cutoff_balance(shared_client):
    from auto_plan.plans import PlanningContext
    client, plans, inputs = shared_client
    capacity(inputs)
    initial = [row(quantity=600) | {'hours': '12'},
               row('b', 'N', quantity=600) | {'order': 1, 'hours': '12'},
               row('locked', 'N', day='2026-10-16', quantity=1200) | {'hours': '24'},
               row('last', 'N', day='2026-10-17', quantity=1200) | {'hours': '24'}]
    assert post(client, 'save', command({'kind': 'patch_plan', 'segments': initial})).status_code == 200
    plans.context = PlanningContext.model_validate({
        'execution': {'facts': [{'date': '2026-10-15', 'line_code': 'L', 'model_code': 'M', 'quantity': 100},
                                {'date': '2026-10-15', 'line_code': 'L', 'model_code': 'N', 'quantity': 300}],
                      'cutoff': '2026-10-15T09:00:00+08:00'},
        'locks': [{'date': '2026-10-16', 'line_code': 'L', 'model_code': 'N', 'quantity': 1200},
                  {'date': '2026-10-16', 'line_code': 'L', 'model_code': 'M', 'quantity': 0}]})
    result = preview(client, change(1000), base=1)
    assert result.status_code == 200, result.text
    data = result.json()
    assert arranged(data) == [('2026-10-15', 'M', 900), ('2026-10-15', 'N', 300),
                              ('2026-10-16', 'N', 1200), ('2026-10-17', 'M', 100),
                              ('2026-10-17', 'N', 300), ('2026-10-17', 'N', 800)]
    assert [(r['model_code'], r['quantity'], r['source_segment_id']) for r in data['unallocated']] == [('N', 400, 'last')]
    assert post(client, 'save', command(change(1000), base=1)).status_code == 200
    stored = client.get('/auto_plan/api/plans/current').json()
    assert stored['unallocated'] == data['unallocated']
    assert post(client, 'save', command(base=2)).status_code == 200
    assert client.get('/auto_plan/api/plans/current').json()['unallocated'] == data['unallocated']


def test_increment_that_fits_does_not_rebuild_later_work_and_decrement_never_pulls_it_forward(shared_client):
    client, _, inputs = shared_client
    capacity(inputs)
    future = row('future', 'N', day='2026-10-16', quantity=100) | {'order': 7, 'hours': '3'}
    assert post(client, 'save', command({'kind': 'patch_plan', 'segments': [row(quantity=600) | {'hours': '12'}, future]})).status_code == 200
    increased = preview(client, change(1000), base=1)
    assert increased.status_code == 200, increased.text
    assert next(r for r in increased.json()['plan']['segments'] if r['id'] == 'future') == future
    decreased = preview(client, change(400), base=1)
    assert decreased.status_code == 200, decreased.text
    assert next(r for r in decreased.json()['plan']['segments'] if r['id'] == 'future') == future
    assert arranged(decreased.json()) == [('2026-10-15', 'M', 400), ('2026-10-16', 'N', 100)]
    unchanged = preview(client, change(600), base=1)
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()['changes'] == []


def test_same_cell_execution_association_is_preserved_when_current_segment_grows(shared_client):
    from auto_plan.plans import PlanningContext
    client, plans, inputs = shared_client
    capacity(inputs, 2000)
    initial = [row(quantity=600) | {'hours': '8'}, row('second', quantity=600) | {'order': 1, 'hours': '8'}]
    assert post(client, 'save', command({'kind': 'patch_plan', 'segments': initial})).status_code == 200
    plans.context = PlanningContext.model_validate({'execution': {
        'facts': [{'date': '2026-10-15', 'line_code': 'L', 'model_code': 'M', 'quantity': 900}],
        'cutoff': '2026-10-15T09:00:00+08:00'}})
    result = preview(client, change(1000), base=1)
    assert result.status_code == 200, result.text
    rows = result.json()['plan']['segments']
    assert next(r['quantity'] for r in rows if r['id'] == 'a') == 600
    assert next(r['quantity'] for r in rows if r['id'] == 'second') == 600
    assert sum(r['quantity'] for r in rows) == 1600
    assert result.json()['unallocated'] == []


def test_chain_crosses_holidays_and_reports_late_confirmation_separately_from_unallocated(shared_client):
    from auto_plan.input_models import InputSnapshot
    client, _, inputs = shared_client
    capacity(inputs)
    data = inputs.read().model_dump(mode='json')
    data['calendar']['holidays'] = [{'date': day, 'holiday': True} for day in ('2026-10-16', '2026-10-17', '2026-10-18')]
    data['business']['demands'] = [{'model_code': 'N', 'week': '2026-10-12', 'quantity': 600}]
    inputs.versions[1] = InputSnapshot.model_validate(data)
    initial = [row(quantity=600) | {'hours': '12'}, row('b', 'N', quantity=600) | {'order': 1, 'hours': '12'}]
    assert post(client, 'save', command({'kind': 'patch_plan', 'segments': initial})).status_code == 200
    result = preview(client, change(1000, '2026-10-19'), base=1)
    assert result.status_code == 200, result.text
    assert arranged(result.json()) == [('2026-10-15', 'M', 1000), ('2026-10-15', 'N', 200), ('2026-10-19', 'N', 400)]
    assert result.json()['unallocated'] == []
    weekly = result.json()['evaluation']['weekly_results'][0]
    assert (weekly['on_time'], weekly['pending_shipping'], weekly['gap']) == (200, 400, 400)
    assert ['REQ', 'N', '2026-10-12'] in result.json()['effects']
    assert ['CAP', 'L', '2026-10-19'] in result.json()['effects']


def test_chain_respects_excluded_models_and_preserves_other_lines_exactly(shared_client):
    client, _, inputs = shared_client
    capacity(inputs)
    fixed = row('b', 'N', quantity=600) | {'order': 1, 'hours': '12'}
    other_line = row('other', 'N', line='B', day='2026-10-16', quantity=100) | {'order': 9, 'hours': '3'}
    assert post(client, 'save', command({'kind': 'patch_plan', 'segments': [row(quantity=600) | {'hours': '12'}, fixed, other_line]})).status_code == 200
    action = change(1000, scope=scope('2026-10-15', '2026-10-17', ['L', 'B']) | {'model_codes': ['M']})
    result = preview(client, action, base=1)
    assert result.status_code == 200, result.text
    segments = result.json()['plan']['segments']
    assert next(r for r in segments if r['id'] == 'b') == fixed
    assert next(r for r in segments if r['id'] == 'other') == other_line
    assert [(r['date'], r['quantity']) for r in segments if r['model_code'] == 'M'] == [('2026-10-15', 600), ('2026-10-16', 400)]


def test_quantity_change_cannot_reduce_executed_or_locked_totals_or_expand_deadline(shared_client):
    from auto_plan.plans import PlanningContext
    client, plans, inputs = shared_client
    capacity(inputs)
    assert post(client, 'save', command({'kind': 'patch_plan', 'segments': [row(quantity=600) | {'hours': '12'}]})).status_code == 200
    plans.context = PlanningContext.model_validate({'execution': {
        'facts': [{'date': '2026-10-15', 'line_code': 'L', 'model_code': 'M', 'quantity': 300}],
        'cutoff': '2026-10-15T09:00:00+08:00'}})
    assert preview(client, change(200), base=1).status_code == 409
    plans.context = PlanningContext.model_validate({'locks': [{'date': '2026-10-15', 'line_code': 'L', 'model_code': 'M', 'quantity': 600}]})
    assert preview(client, change(1000), base=1).status_code == 409
    assert preview(client, change(400), base=1).status_code == 409
    invalid = change(1000) | {'end_date': '2026-10-18'}
    assert preview(client, invalid, base=1).status_code == 422
    assert client.get('/auto_plan/api/plans/current').json()['plan']['segments'][0]['quantity'] == 600


def test_exact_recipe_rounding_and_directed_switch_allow_partial_carry(shared_client):
    from auto_plan.input_models import InputSnapshot
    client, _, inputs = shared_client
    data = inputs.read().model_dump(mode='json')
    data['calendar']['maintenance'] = [{'date': day, 'line_code': 'L', 'hours': 23} for day in ('2026-10-15', '2026-10-16')]
    inputs.versions[1] = InputSnapshot.model_validate(data)
    initial = {'segments': [row(quantity=1) | {'hours': '0.025'}, row('b', 'P', quantity=2) | {'order': 2, 'hours': '0.025'}],
               'switches': [{'id': 'initial-switch', 'line_code': 'L', 'from_recipe': 'R', 'to_recipe': 'S',
                             'allocations': [{'date': '2026-10-15', 'order': 1, 'hours': '0.95'}]}]}
    assert post(client, 'save', command({'kind': 'replace_plan', 'plan': initial})).status_code == 200
    result = preview(client, change(3, '2026-10-16'), base=1)
    assert result.status_code == 200, result.text
    assert arranged(result.json()) == [('2026-10-15', 'M', 3), ('2026-10-15', 'P', 1), ('2026-10-16', 'P', 1)]
    assert result.json()['unallocated'] == []
    assert result.json()['evaluation']['capacity_valid'] is True
    assert result.json()['evaluation']['switch_count'] == 1


def test_quantity_lock_reserves_only_its_aggregate_and_allows_the_remaining_capacity(shared_client):
    from auto_plan.plans import PlanningContext
    client, plans, inputs = shared_client
    capacity(inputs)
    initial = [row(quantity=600) | {'hours': '12'}, row('b', 'N', quantity=600) | {'order': 1, 'hours': '12'},
               row('locked', 'N', day='2026-10-16', quantity=600) | {'hours': '12'}]
    assert post(client, 'save', command({'kind': 'patch_plan', 'segments': initial})).status_code == 200
    plans.context = PlanningContext.model_validate({'locks': [{'date': '2026-10-16', 'line_code': 'L', 'model_code': 'N', 'quantity': 600}]})
    result = preview(client, change(1400, '2026-10-16'), base=1)
    assert result.status_code == 200, result.text
    assert arranged(result.json()) == [('2026-10-15', 'M', 1200), ('2026-10-16', 'M', 200), ('2026-10-16', 'N', 600)]
    assert [(r['model_code'], r['quantity']) for r in result.json()['unallocated']] == [('N', 600)]


def test_fitting_increment_keeps_later_locked_recipe_work_verbatim(shared_client):
    from auto_plan.plans import PlanningContext
    client, plans, inputs = shared_client
    capacity(inputs)
    future = row('future', 'P', day='2026-10-16', quantity=100) | {'order': 7, 'hours': '3'}
    switch = {'id': 'future-switch', 'line_code': 'L', 'from_recipe': 'R', 'to_recipe': 'S',
              'allocations': [{'date': '2026-10-16', 'order': 6, 'hours': '0.95', 'idle_hours': '0'}]}
    initial = {'segments': [row(quantity=600) | {'hours': '12'}, future], 'switches': [switch]}
    assert post(client, 'save', command({'kind': 'replace_plan', 'plan': initial})).status_code == 200
    original_switches = client.get('/auto_plan/api/plans/current').json()['plan']['switches']
    plans.context = PlanningContext.model_validate({'locks': [{'date': '2026-10-16', 'line_code': 'L', 'model_code': 'P', 'quantity': 100}]})
    result = preview(client, change(1000), base=1)
    assert result.status_code == 200, result.text
    assert next(r for r in result.json()['plan']['segments'] if r['id'] == 'future') == future
    assert result.json()['plan']['switches'] == original_switches


def test_chain_rejects_boundary_recipe_repair_outside_the_explicit_deadline(shared_client):
    client, _, inputs = shared_client
    capacity(inputs)
    initial = {'segments': [row(quantity=600) | {'hours': '12'},
                            row('b', 'P', quantity=500) | {'order': 2, 'hours': '10'},
                            row('outside', 'P', day='2026-10-16', quantity=100)],
               'switches': [{'id': 'switch', 'line_code': 'L', 'from_recipe': 'R', 'to_recipe': 'S',
                             'allocations': [{'date': '2026-10-15', 'order': 1, 'hours': '0.95'}]}]}
    assert post(client, 'save', command({'kind': 'replace_plan', 'plan': initial})).status_code == 200
    original = client.get('/auto_plan/api/plans/current').json()['plan']
    result = preview(client, change(1200, '2026-10-15'), base=1)
    assert result.status_code == 422, result.text
    assert '扩大范围' in result.json()['detail']
    assert client.get('/auto_plan/api/plans/current').json()['plan'] == original


def test_decrement_keeps_other_authorized_lines_and_their_switch_work_unchanged(shared_client):
    client, _, inputs = shared_client
    capacity(inputs)
    initial = {'initial_states': [{'line_code': 'B', 'recipe_code': 'R'}],
               'segments': [row(quantity=600) | {'hours': '12'}, row('other', 'P', line='B', day='2026-10-16', quantity=100) | {'order': 7}],
               'switches': [{'id': 'other-switch', 'line_code': 'B', 'from_recipe': 'R', 'to_recipe': 'S',
                             'allocations': [{'date': '2026-10-16', 'order': 6, 'hours': '0.95'}]}]}
    assert post(client, 'save', command({'kind': 'replace_plan', 'plan': initial})).status_code == 200
    original = client.get('/auto_plan/api/plans/current').json()['plan']
    result = preview(client, change(400, scope=scope('2026-10-15', '2026-10-17', ['L', 'B'])), base=1)
    assert result.status_code == 200, result.text
    assert [r for r in result.json()['plan']['segments'] if r['line_code'] == 'B'] == [r for r in original['segments'] if r['line_code'] == 'B']
    assert result.json()['plan']['switches'] == original['switches']


def test_extra_plan_does_not_reassign_an_existing_segments_overplan_actual(shared_client):
    from auto_plan.plans import PlanningContext
    client, plans, inputs = shared_client
    capacity(inputs)
    assert post(client, 'save', command({'kind': 'patch_plan', 'segments': [row(quantity=600) | {'hours': '12'}]})).status_code == 200
    plans.context = PlanningContext.model_validate({'execution': {
        'facts': [{'date': '2026-10-15', 'line_code': 'L', 'model_code': 'M', 'quantity': 900}],
        'cutoff': '2026-10-15T09:00:00+08:00'}})
    result = preview(client, change(1000), base=1)
    assert result.status_code == 200, result.text
    rows = result.json()['plan']['segments']
    assert next(r['quantity'] for r in rows if r['id'] == 'a') == 600
    assert sum(r['quantity'] for r in rows) == 1000
    assert result.json()['evaluation']['reconciled_cells'][0]['actual'] == 900


def test_inactive_masters_remain_visible_but_cannot_gain_new_quantity_or_positions(shared_client):
    from auto_plan.input_models import InputSnapshot
    client, _, inputs = shared_client
    capacity(inputs)
    initial = [row(quantity=600) | {'hours': '12'}, row('b', 'N', quantity=600) | {'order': 1, 'hours': '12'}]
    assert post(client, 'save', command({'kind': 'patch_plan', 'segments': initial})).status_code == 200
    data = inputs.read().model_dump(mode='json')
    data['masters']['models'][0]['active'] = False
    inputs.versions[1] = InputSnapshot.model_validate(data)
    assert preview(client, change(1000), base=1).status_code == 422
    assert preview(client, change(400), base=1).status_code == 200
    data['masters']['models'][0]['active'] = True
    data['masters']['models'][1]['active'] = False
    inputs.versions[1] = InputSnapshot.model_validate(data)
    assert preview(client, change(1000), base=1).status_code == 422


def test_explicit_unlock_must_precede_quantity_change_in_the_same_complete_queue(shared_client):
    client, _, inputs = shared_client
    capacity(inputs)
    initial = [row(quantity=600) | {'hours': '12'}, row('b', 'N', quantity=600) | {'order': 1, 'hours': '12'}]
    assert post(client, 'save', command({'kind': 'patch_plan', 'segments': initial})).status_code == 200
    cell = {'date': '2026-10-15', 'line_code': 'L', 'model_code': 'M'}
    lock = {'kind': 'lock_cells', 'cells': [cell | {'quantity': 600}]}
    assert post(client, 'save', command(lock, base=1)).status_code == 200
    unlock = {'kind': 'unlock_cells', 'cells': [cell]}
    late = post(client, 'preview', {k: v for k, v in command(change(1000), unlock, base=2, version=2).items() if k not in ('operation_id', 'pending')})
    assert late.status_code == 409
    payload = command(unlock, change(1000), base=2, version=2)
    result = post(client, 'preview', {k: v for k, v in payload.items() if k not in ('operation_id', 'pending')})
    assert result.status_code == 200, result.text
    assert arranged(result.json()) == [('2026-10-15', 'M', 1000), ('2026-10-15', 'N', 200), ('2026-10-16', 'N', 400)]
    assert result.json()['context']['locks'] == []
    assert client.get('/auto_plan/api/plans/current').json()['context']['locks'] == lock['cells']
    saved = post(client, 'save', payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()['input_version'] == 3
    current = client.get('/auto_plan/api/plans/current').json()
    assert current['context']['locks'] == []
    assert current['plan'] == result.json()['plan']


def test_locking_before_cascade_protects_that_aggregate_in_the_same_queue(shared_client):
    client, _, inputs = shared_client
    capacity(inputs)
    initial = [row(quantity=600) | {'hours': '12'}, row('b', 'N', quantity=600) | {'order': 1, 'hours': '12'}]
    assert post(client, 'save', command({'kind': 'patch_plan', 'segments': initial})).status_code == 200
    cell = {'date': '2026-10-15', 'line_code': 'L', 'model_code': 'N', 'quantity': 600}
    lock = {'kind': 'lock_cells', 'cells': [cell]}
    payload = command(lock, change(1000, '2026-10-16'), base=1)
    result = post(client, 'preview', {k: v for k, v in payload.items() if k not in ('operation_id', 'pending')})
    assert result.status_code == 200, result.text
    assert arranged(result.json()) == [('2026-10-15', 'M', 600), ('2026-10-15', 'N', 600), ('2026-10-16', 'M', 400)]
    assert result.json()['context']['locks'] == [cell]
    assert result.json()['unallocated'] == []
    saved = post(client, 'save', payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()['input_version'] == 2
    current = client.get('/auto_plan/api/plans/current').json()
    assert current['context']['locks'] == [cell]
    assert current['plan'] == result.json()['plan']
