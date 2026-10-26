"""Explicit late alternatives through the public planning boundary."""
from auto_plan.input_models import InputSnapshot
from tests.test_plan_transactions import shared_client, post
from tests.test_execution_reset import current_command, head
from tests.test_demand_trials import configure, trial
from tests.test_output_preview import confirmation
from tests.line_state_support import confirm_states


def test_explicit_late_alternative_keeps_on_time_rate_and_partial_shipping_gap(shared_client):
    client, _, inputs = shared_client
    data = configure(inputs, demand=1000, line_yield='100', model_yield='100', capacity=600)
    data['business']['confirmations'] = [confirmation('shipping', '2026-10-12', 200, '2026-10-19')]
    inputs.versions[1] = InputSnapshot.model_validate(data)
    selected = dict(target_output=1000, operations=[confirm_states()], start_date='2026-10-18', end_date='2026-10-19',
                    scope={'start_date':'2026-10-18','end_date':'2026-10-19','line_codes':['L'],'model_codes':['M']})
    original = head(client)
    ordinary = trial(client, **selected)
    assert ordinary.status_code == 200, ordinary.text
    assert (ordinary.json()['suggested_input'], ordinary.json()['scheduled_on_time_output'], ordinary.json()['remaining_output_target']) == (600, 600, 400)

    checked = trial(client, mode='late', **selected)

    assert checked.status_code == 200, checked.text
    result = checked.json()
    assert (result['suggested_input'], result['scheduled_on_time_output'], result['scheduled_late_output'], result['remaining_output_target']) == (1000, 600, 400, 0)
    assert (result['valid_on_time_output'], result['valid_late_output']) == (600, 200)
    row = result['selected_demand']
    assert (row['on_time'], row['late'], row['gap'], row['pending_shipping'], row['on_time_rate']) == (600, 200, 200, 200, '3/5')
    assert [(row['date'],row['quantity']) for row in result['placements']] == [('2026-10-18',600),('2026-10-19',400)]
    assert result['operation']['mode'] == 'late'
    assert head(client) == original
    command = current_command(client, confirm_states(), result['operation'])
    saved = post(client, 'save', command)
    assert saved.status_code == 200, saved.text
    assert post(client, 'adopt', current_command(client)).status_code == 422
    assert head(client)['latest_adoption'] == 0
