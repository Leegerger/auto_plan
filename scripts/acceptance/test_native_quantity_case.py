"""Local fixture/raw-progress controls; these never start an application or job."""
import hashlib
import json
from types import SimpleNamespace
import pytest
from scripts.acceptance.native_quantity_case import quantity_inputs, quantity_command, native_quantity_stage, quantity_objective, PublicJobProof


def test_literal_source_and_command_match_existing_native_quantity_business_case():
    inputs = quantity_inputs().model_dump(mode='json')
    assert inputs['input_version'] == 1
    assert inputs['business']['demands'] == [{'model_code':'M','week':'2026-10-12','quantity':1000}]
    assert len(inputs['parameters']['capacities']) == 2
    assert all(row['pieces'] == 2000 for row in inputs['parameters']['capacities'])
    command = quantity_command({'snapshot': {'time': {'as_of':'2026-10-14T00:00:00Z'}}}, 'original-uuid')
    assert command == {'submission_id':'original-uuid','base_revision':0,'input_version':1,'latest_adoption':0,'reviewed_as_of':'2026-10-14T00:00:00Z','request':{'mode':'local','scope':{'start_date':'2026-10-15','end_date':'2026-10-15','line_codes':['L'],'model_codes':[]},'strategy':'cp_sat','time_limit_seconds':20,'max_candidates':1,'native_threads':1}}


def test_raw_failure_is_retained_before_parse_or_assertion(tmp_path):
    proof = PublicJobProof(tmp_path/'raw')
    response = SimpleNamespace(content=b'{malformed actual response', status_code=503)
    with pytest.raises(json.JSONDecodeError):
        proof.capture('candidate', response, {'submission_id':'original-uuid'})
    row = json.loads((proof.directory/'index.ndjson').read_text())
    assert row['status'] == 503
    assert (proof.directory/row['body']['path']).read_bytes() == response.content
    assert row['body']['sha256'] == hashlib.sha256(response.content).hexdigest()
    assert row['body']['bytes'] == len(response.content)
    assert json.loads((proof.directory/row['command']['path']).read_text()) == {'submission_id':'original-uuid'}


def test_native_progress_requires_exact_actual_run_and_complete_layer_evidence():
    owner = {'job_id':'J','run_id':'R','generation':'G'}
    event = {'kind':'progress',**owner,'stage':'native_local','status':'restricted_model','route':'cp_sat','native_layers':[{'layer':n,'native_value':'0','reported_optimal':True,'exact_lower_bound':'0'} for n in range(33)]}
    assert native_quantity_stage([event], owner) == event
    for change in [{'job_id':'OTHER'},{'run_id':'OTHER'},{'generation':'OTHER'},{'route':None},{'stage':'order_after_quantity'},{'native_layers':[]},{'status':'bounded_model_rejected'}]:
        with pytest.raises(ValueError):
            native_quantity_stage([event | change], owner)


def test_single_demand_week_still_has_all_fifteen_objective_coordinates():
    evaluation = {'weekly_results':[{'week':'2026-10-12','demand':1000,'on_time':998,'gap':2}], 'switch_count':3,'stability':{'weighted_cost':'5/2','order_change_count':1}}
    expected = ['0']*33
    expected[0] = expected[15] = '2'
    expected[30:] = ['3','5/2','1']
    assert quantity_objective(evaluation, '2026-10-12') == expected
