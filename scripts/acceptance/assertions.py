"""Acceptance-only bindings for public snapshots, candidates and workbook previews."""
from fractions import Fraction
from .compare import numeric, objective, compare_vector


def snapshot_value(value):
    from auto_plan.plan_models import EvaluationSnapshot
    result = EvaluationSnapshot.model_validate(value).model_dump(mode='json')
    for key in ('segments', 'switches'):
        result['plan'][key].sort(key=lambda row: row['id'])
    result['plan']['initial_states'].sort(key=lambda row: row['line_code'])
    return numeric(result)


def assert_frozen_snapshot(observed, source):
    frozen, actual = snapshot_value(source), snapshot_value(observed)
    actual['inputs']['input_version'] = frozen['inputs']['input_version']
    if actual['baseline']:
        assert frozen['baseline'], 'An unexpected baseline appeared.'
        actual['baseline']['version'] = frozen['baseline']['version']
    # Public first-import zero WIP/balance is an explicit equivalent of no reset output.
    for value in (frozen, actual):
        if value['reset'] and all(not row['wip'] and not row['net_balance'] for row in value['reset']['models']):
            value['reset'] = None
    assert actual == frozen, 'Frozen complete input/context/baseline/plan differs.'


def assert_provenance(proof, source, action, evaluation):
    from auto_plan.solver_models import SolveRequest
    candidate, submitted, state = proof['candidate'], proof['submitted'], proof['source']
    for key, name in [('source_revision', 'base_revision'), ('input_version', 'input_version'), ('latest_adoption', 'latest_adoption')]:
        assert candidate[key] == submitted[name], 'Candidate frozen source tokens differ from the actual submission.'
        head_key = 'revision' if name == 'base_revision' else name
        assert submitted[name] == state['head'][head_key], 'Submission differs from the captured live source tokens.'
    assert candidate['snapshot']['inputs']['input_version'] == submitted['input_version'], 'Candidate snapshot differs from the actual accepted input pin.'
    assert state['snapshot']['inputs']['input_version'] == submitted['input_version'], 'Captured source snapshot differs from the actual accepted input pin.'
    for snapshot in (candidate['snapshot'], state['snapshot']):
        assert (snapshot['baseline']['version'] if snapshot['baseline'] else 0) == submitted['latest_adoption'], 'Snapshot baseline differs from the actual accepted adoption pin.'
    expected_request = SolveRequest.model_validate(action['request']).model_dump(mode='json')
    assert SolveRequest.model_validate(submitted['request']).model_dump(mode='json') == expected_request, 'Submitted mode/scope/budget differs from frozen action.'
    assert SolveRequest.model_validate(candidate['request']).model_dump(mode='json') == expected_request, 'Candidate mode/scope/budget differs from frozen action.'
    assert_frozen_snapshot(candidate['snapshot'], source)
    assert snapshot_value(candidate['snapshot']) == snapshot_value(state['snapshot']), 'Candidate snapshot differs from the captured live submission source.'
    actual = objective(evaluation)
    assert compare_vector(candidate['objective'], actual) == 0, 'Returned candidate objective differs from independent public re-evaluation.'
    assert compare_vector(objective(candidate['evaluation']), actual) == 0, 'Returned candidate evaluation differs from public re-evaluation.'
    for key in ('capacity_valid', 'computation_complete', 'executable'):
        assert candidate['evaluation'][key] == evaluation[key], 'Returned candidate hard-state flags differ.'
    for key in ('expected', 'on_time', 'late', 'gap', 'pending', 'unallocated', 'outside_window'):
        assert candidate['evaluation']['output_totals'][key] == evaluation['output_totals'][key], 'Returned candidate output totals differ.'
    fields = ('model_code', 'week', 'demand', 'attributed', 'on_time', 'late', 'pending_material', 'pending_shipping', 'pending', 'gap')
    def weekly(rows):
        values = sorted(tuple(row.get(key) for key in fields) for row in rows)
        assert len(values) == len({(row['model_code'], row['week']) for row in rows}), 'Returned candidate weekly identities repeat.'
        return values
    assert weekly(candidate['evaluation']['weekly_results']) == weekly(evaluation['weekly_results']), 'Returned candidate weekly coverage differs.'
    return actual


def payload_value(value, key=''):
    if isinstance(value, list):
        rows = [payload_value(item) for item in value]
        return sorted(rows, key=repr)
    if isinstance(value, dict):
        return {name: payload_value(item, name) for name, item in sorted(value.items())
                if name not in {'_row', 'week_label'} and item is not None}
    if key in {'hours', 'percent', 'days'}:
        return Fraction(str(value))
    if key in {'quantity', 'pieces', 'order', 'wip', 'net_balance', 'base_revision', 'input_version', 'latest_adoption'}:
        return int(value)
    if key in {'active', 'allowed', 'enabled', 'holiday', 'blanks_are_zero', 'complete_weekly_demands'}:
        if isinstance(value, str):
            assert value.lower() in {'true', 'false'}, 'A workbook boolean is invalid.'
            return value.lower() == 'true'
    return value


def assert_workbook(prepared, expected, source, scenario, before, after):
    assert scenario['kind'] == 'loose', 'No independently frozen workbook result is supplied for this family.'
    assert prepared['valid'] is True, 'Workbook preview is invalid.'
    expected = expected | {'base_revision': before['head']['revision'], 'input_version': before['head']['input_version'], 'latest_adoption': before['head']['latest_adoption']}
    assert payload_value(prepared['payload']) == payload_value(expected), 'Workbook payload differs from the complete frozen import.'
    from auto_plan.input_models import InputSnapshot
    from auto_plan.plan_models import ExecutionContext, ResetContext
    expected_inputs = InputSnapshot.model_validate(source['inputs']).model_dump(mode='json')
    actual_inputs = InputSnapshot.model_validate(prepared['inputs']).model_dump(mode='json')
    actual_inputs['input_version'] = expected_inputs['input_version']
    assert payload_value(actual_inputs) == payload_value(expected_inputs), 'Workbook prepared complete inputs differ.'
    assert payload_value(ExecutionContext.model_validate(prepared['context']['execution']).model_dump(mode='json')) == payload_value(ExecutionContext.model_validate(source['execution']).model_dump(mode='json')), 'Workbook prepared execution differs.'
    assert payload_value(ResetContext.model_validate(prepared['context']['reset']).model_dump(mode='json')) == payload_value(ResetContext.model_validate(expected['reset']).model_dump(mode='json')), 'Workbook prepared reset differs.'
    assert payload_value(prepared['context']['coverage']) == payload_value(expected['coverage']), 'Workbook prepared coverage differs.'
    assert payload_value(prepared['plan']['initial_states']) == payload_value(source['plan']['initial_states']), 'Workbook prepared initial states differ.'
    assert len(prepared['plan']['segments']) == 4200, 'Workbook is not the complete 4200-segment source.'
    wanted = [{key: row[key] for key in ('id', 'date', 'line_code', 'model_code', 'quantity')} for row in source['plan']['segments']]
    actual = [{key: row[key] for key in ('id', 'date', 'line_code', 'model_code', 'quantity')} for row in prepared['plan']['segments']]
    assert payload_value(actual) == payload_value(wanted), 'Workbook prepared source segment identities differ.'
    # The standard import allocates new switch/hour witnesses and inserts order
    # slots between production rows. Its complete production sequence must match.
    def sequence(rows):
        return [row['id'] for row in sorted(rows, key=lambda row: (row['date'], row['line_code'], row['order']))]
    assert sequence(prepared['plan']['segments']) == sequence(source['plan']['segments']), 'Workbook prepared production order differs.'
    from .compare import assert_reference
    assert_reference(prepared['evaluation'], scenario)
    for key in ('revision', 'input_version', 'latest_adoption'):
        assert before['head'][key] == after['head'][key], 'Import preview changed a shared token.'
    assert before['head']['revision'] == before['head']['latest_adoption'] == 0, 'First import target was not empty.'
    assert payload_value(before['snapshot']) == payload_value(after['snapshot']), 'Import preview changed source input/context/plan contents.'
