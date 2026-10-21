"""Untrusted public workbook/candidate replies must bind to the complete frozen inputs."""
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
import json
import pytest
from scripts.acceptance.validate import load
from scripts.acceptance.assertions import assert_workbook, assert_provenance

FIXTURES = Path(__file__).resolve().parents[2] / 'docs/acceptance/frozen'
SCENARIO = next(row for row in json.loads((FIXTURES / 'manifest.json').read_text())['scenarios'] if row['id'] == 'r20-loose')


def known_evaluation():
    return {'capacity_valid': True, 'computation_complete': True, 'executable': True, 'switch_count': 418,
            'output_totals': {'expected': 420000, 'on_time': 420000, 'late': 0, 'gap': 0, 'pending': 0, 'unallocated': 0, 'outside_window': 0},
            'stability': {'weighted_cost': '0', 'order_change_count': 0},
            'weekly_results': [{'model_code': f'M{model:02d}', 'week': (date(2026, 10, 12) + timedelta(weeks=week)).isoformat(), 'demand': 700, 'on_time': 700, 'gap': 0} for week in range(15) for model in range(40)]}


def test_valid_smaller_workbook_and_changed_capacity_cannot_replace_the_frozen_full_import():
    source, payload = load(FIXTURES, SCENARIO['source']), load(FIXTURES, SCENARIO['import'])
    prepared = {'valid': True, 'payload': payload, 'plan': source['plan'], 'evaluation': known_evaluation(), 'inputs': source['inputs'],
                'context': {'execution': source['execution'], 'reset': payload['reset'], 'coverage': payload['coverage']}}
    before = {'head': {'revision': 0, 'input_version': 0, 'latest_adoption': 0}, 'snapshot': {'inputs': {'input_version': 0}, 'plan': {'segments': []}}}
    assert_workbook(prepared, payload, source, SCENARIO, before, deepcopy(before))
    small = deepcopy(prepared); small['payload']['segments'] = small['payload']['segments'][:1]
    with pytest.raises(AssertionError, match='payload differs'):
        assert_workbook(small, payload, source, SCENARIO, before, deepcopy(before))
    changed = deepcopy(prepared); changed['payload']['changes']['capacities'][0]['pieces'] = 1
    with pytest.raises(AssertionError, match='payload differs'):
        assert_workbook(changed, payload, source, SCENARIO, before, deepcopy(before))
    after = deepcopy(before); after['snapshot']['inputs']['unexpected'] = True
    with pytest.raises(AssertionError, match='contents'):
        assert_workbook(prepared, payload, source, SCENARIO, before, after)


def test_candidate_requires_matching_live_frozen_snapshot_request_and_all_33_objective_coordinates():
    source, action = load(FIXTURES, SCENARIO['source']), load(FIXTURES, SCENARIO['action'])
    evaluation = known_evaluation()
    candidate = {'source_revision': 7, 'input_version': 1, 'latest_adoption': 1, 'request': action['request'], 'snapshot': source,
                 'objective': ['0'] * 30 + ['418', '0', '0'], 'evaluation': evaluation}
    proof = {'candidate': candidate, 'submitted': {'base_revision': 7, 'input_version': 1, 'latest_adoption': 1, 'request': action['request']},
             'source': {'snapshot': source, 'head': {'revision': 7, 'input_version': 1, 'latest_adoption': 1}}}
    assert_provenance(proof, source, action, evaluation)
    bad = deepcopy(proof); bad['candidate']['snapshot']['inputs']['parameters']['model_yields'][0]['percent'] = '99'
    with pytest.raises(AssertionError, match='complete input'):
        assert_provenance(bad, source, action, evaluation)
    wrong_scope = deepcopy(proof); wrong_scope['candidate']['request']['scope']['end_date'] = '2027-01-23'
    with pytest.raises(AssertionError, match='mode/scope'):
        assert_provenance(wrong_scope, source, action, evaluation)
    wrong_vector = deepcopy(proof); wrong_vector['candidate']['objective'][32] = '1'
    with pytest.raises(AssertionError, match='objective differs'):
        assert_provenance(wrong_vector, source, action, evaluation)
    wrong_revision = deepcopy(proof); wrong_revision['source']['head']['revision'] = 8
    with pytest.raises(AssertionError, match='live source tokens'):
        assert_provenance(wrong_revision, source, action, evaluation)


def test_candidate_snapshots_must_use_the_actual_accepted_input_pin():
    source, action = load(FIXTURES, SCENARIO['source']), load(FIXTURES, SCENARIO['action'])
    evaluation = known_evaluation()
    snapshot = deepcopy(source)
    # A later displayed input read cannot be substituted for the accepted v1,
    # even when its quantities happen to match and both snapshots agree with it.
    snapshot['inputs']['input_version'] = 2
    proof = {'candidate': {'source_revision': 7, 'input_version': 1, 'latest_adoption': 1,
                          'request': action['request'], 'snapshot': deepcopy(snapshot),
                          'objective': ['0'] * 30 + ['418', '0', '0'], 'evaluation': evaluation},
             'submitted': {'base_revision': 7, 'input_version': 1, 'latest_adoption': 1, 'request': action['request']},
             'source': {'snapshot': deepcopy(snapshot), 'head': {'revision': 7, 'input_version': 1, 'latest_adoption': 1}}}
    with pytest.raises(AssertionError, match='accepted input|input pin'):
        assert_provenance(proof, source, action, evaluation)


def test_frozen_workbook_public_roundtrip_retains_all_rows_and_does_not_write(tmp_path):
    from scripts.acceptance.validate import public_client
    from scripts.acceptance.workbook import workbook
    source = load(FIXTURES, SCENARIO['source'])
    with public_client(source, empty=True) as client:
        def state():
            head = client.get('/auto_plan/api/plans/current').json()
            inputs = client.get('/auto_plan/api/inputs?version=' + str(head['input_version'])).json()
            return {'head': head, 'snapshot': {'inputs': inputs, 'plan': head['plan'], 'context': head['context']}}
        before = state()
        template = tmp_path / 'standard.xlsx'
        template.write_bytes(client.get('/auto_plan/api/initialization/template.xlsx').content)
        prepared_file = tmp_path / 'r20.xlsx'
        workbook(FIXTURES, SCENARIO['id'], template, prepared_file)
        response = client.post('/auto_plan/api/initialization/excel/preview',
                               params={key: before['head'][key] for key in ('input_version', 'latest_adoption')} | {'base_revision': before['head']['revision']},
                               content=prepared_file.read_bytes(), headers={'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'})
        assert response.status_code == 200, response.text
        assert_workbook(response.json(), load(FIXTURES, SCENARIO['import']), source, SCENARIO, before, state())
