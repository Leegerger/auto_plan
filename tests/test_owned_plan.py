"""Public ownership and validation with an encoded-record Oracle unit adapter.

No actual Oracle connection is made. Private call/identity assertions qualify
validation reuse; full public behavior and the existing timing RED remain the
acceptance criteria. These adapters never connect to Oracle.
"""
from tests.line_state_support import confirm_states
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid4
from types import SimpleNamespace
import zlib
import pytest
from pydantic import ValidationError

from auto_plan.input_models import InputSnapshot
from auto_plan.oracle_plans import OraclePlanUnit, document, encode
from auto_plan.plan_models import EvaluationSnapshot, Plan
from auto_plan.plans import PlanService
from auto_plan.rules import evaluate
from tests.jobs_support import MemoryJobStore
from tests.test_plan_transactions import shared_client, command, post, row


class EncodedRecordCursor:
    """Only immutable record/effect reads, not a database or concurrency model."""
    def __init__(self, plans):
        self.revisions = deepcopy(plans.revisions)
        self.adoptions = deepcopy(plans.adoptions)
        self.rows = []

    def execute(self, sql, **binds):
        if 'SELECT EFFECT_JSON FROM AP_PLAN_EFFECT' in sql:
            self.rows = [(json.dumps(effect),)
                         for number, record in self.revisions.items() if number > binds['revision']
                         for effect in record.get('effects', [])]
            return
        assert 'SELECT S.SNAPSHOT_BLOB,S.SHA256' in sql, sql
        records = self.adoptions if 'FROM AP_PLAN_ADOPTION ' in sql else self.revisions
        record = records.get(binds['identity'])
        self.rows = [] if record is None else [(encode(record), hashlib.sha256(document(record)).hexdigest())]

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def __iter__(self):
        return iter(self.rows)


def test_public_owned_current_plan_reuse_preserves_adoption_sources_and_fallbacks(shared_client, monkeypatch):
    client, plans, inputs = shared_client
    data = inputs.read().model_dump(mode='json')
    data['business']['demands'] = [
        {'model_code': model, 'week': '2026-10-12', 'quantity': 100}
        for model in ('M', 'N')
    ]
    inputs.versions[1] = InputSnapshot.model_validate(data)
    saved = post(client, 'save', command({'kind': 'replace_plan', 'plan': {
        'segments': [row('m'), row('n', 'N', '2026-10-16')],
        'initial_states': [{'line_code': 'L', 'recipe_code': 'R'}],
    }}))
    assert saved.status_code == 200, saved.text
    adopted = post(client, 'adopt', command(confirm_states(), base=saved.json()['revision']))
    assert adopted.status_code == 200, adopted.text
    adoption = client.get('/auto_plan/api/plans/adoptions/1').json()
    normal_read = plans.read
    units = []
    reuse_enabled = [True]

    def scoped_read(actor, action):
        assert actor == 'EDITOR'
        unit = OraclePlanUnit(plans, EncodedRecordCursor(plans),
                              (plans.revision, plans.adoption), inputs.read().model_copy(deep=True),
                              plans.context.model_copy(deep=True))
        if not reuse_enabled[0]:
            # Force the existing raw-value/full-validation fallback, rather
            # than disabling a validator or changing a result.
            unit.planning_plan_value = lambda record: record['snapshot']['plan']
        before = unit.plan.model_dump(mode='json')
        result = action(unit)
        assert unit.plan.model_dump(mode='json') == before
        units.append(unit)
        return result

    monkeypatch.setattr(plans, 'read', scoped_read)
    before = client.get('/auto_plan/api/plans/current').json()
    assert before['evaluation']['output_totals']['expected'] == 200
    request = {'base_revision': before['revision'], 'input_version': before['input_version'],
               'latest_adoption': before['latest_adoption'], 'operations': [{
                   'kind': 'change_quantity', 'segment_id': 'm', 'quantity': 99,
                   'end_date': '2026-10-15', 'action_id': str(uuid4()),
                   'scope': {'start_date': '2026-10-15', 'end_date': '2026-10-15',
                             'line_codes': ['L'], 'model_codes': ['M']},
               }]}
    raw_validations = []
    original_validate = Plan.model_validate.__func__

    def traced_validate(cls, value, *args, **kwargs):
        if type(value) is dict:
            raw_validations.append(deepcopy(value))
        return original_validate(cls, value, *args, **kwargs)

    with monkeypatch.context() as tracer:
        tracer.setattr(Plan, 'model_validate', classmethod(traced_validate))
        response = post(client, 'preview', request)
    assert response.status_code == 200, response.text
    actual = response.json()
    totals = actual['evaluation']['output_totals']
    assert (totals['expected'], totals['on_time'], totals['gap']) == (199, 199, 1)
    assert actual['snapshot']['baseline']['plan'] == adoption['snapshot']['plan']
    assert actual['snapshot']['baseline']['plan'] != actual['plan']
    assert actual['evaluation'] == evaluate(
        EvaluationSnapshot.model_validate(actual['snapshot'])
    ).model_dump(mode='json')
    reuse_enabled[0] = False
    try:
        fresh = post(client, 'preview', request)
        assert fresh.status_code == 200, fresh.text
        assert fresh.json() == actual
    finally:
        reuse_enabled[0] = True
    assert client.get('/auto_plan/api/plans/current').json() == before
    assert client.get('/auto_plan/api/plans/adoptions/1').json() == adoption

    # This detects repeated raw validation after the behavioral assertions.
    # It is optimization-path qualification, not a fabricated semantic failure.
    assert len(raw_validations) == 1
    scope = {'start_date':'2026-10-15','end_date':'2026-10-20',
             'line_codes':['L','B'],'model_codes':['M','N']}
    for operation, quantity in [
        ({'kind':'append_fill','model_code':'M','quantity':1,'line_code':'L',
          'start_date':'2026-10-15','end_date':'2026-10-15'}, 201),
        ({'kind':'copy_segments','segment_ids':['m'],'target_start_date':'2026-10-17',
          'line_mapping':{'L':'B'}}, 300),
        ({'kind':'split_segment','segment_id':'m','quantity':50,'target_date':'2026-10-16',
          'target_line':'B','target_order':0}, 200),
        ({'kind':'move_segment','segment_id':'m','target_date':'2026-10-16',
          'target_line':'B','target_order':0}, 200),
        ({'kind':'clear_segments','segment_ids':['m']}, 100),
    ]:
        edit = request | {'operations':[operation | {'scope':scope,'action_id':str(uuid4())}]}
        borrowed = post(client, 'preview', edit)
        assert borrowed.status_code == 200, borrowed.text
        assert sum(segment['quantity'] for segment in borrowed.json()['plan']['segments']) == quantity
        reuse_enabled[0] = False
        try:
            owned = post(client, 'preview', edit)
            assert owned.status_code == 200, owned.text
            assert owned.json() == borrowed.json()
        finally:
            reuse_enabled[0] = True
        assert client.get('/auto_plan/api/plans/current').json() == before
        assert client.get('/auto_plan/api/plans/adoptions/1').json() == adoption
    service = PlanService(plans)
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    unit = units[-1]
    snapshot = service._snapshot(unit, unit.plan, now)
    assert snapshot.baseline.plan is unit.plan
    full = unit.get_revision(unit.revision)
    selected = unit.get_planning_revision(unit.revision)
    assert 'evaluation' in full and 'evaluation' not in selected
    assert full == plans.get_revision(unit.revision)
    assert type(unit.planning_plan_value(full)) is dict

    # Same complete raw Plan, different verified complete record/context SHA.
    changed = post(client, 'save', command(
        {'kind': 'set_preferences', 'preferences': {'near_week_weight': '7'}},
        base=before['revision'], version=before['input_version'], adoption=1))
    assert changed.status_code == 200, changed.text
    client.app.state.job_store = MemoryJobStore(plans, client.app.state.access_store)
    source = client.get('/auto_plan/api/jobs/source')
    assert source.status_code == 200, source.text
    value = source.json()
    unit = units[-1]
    current_record = unit.get_planning_revision(unit.revision)
    baseline_record = unit.get_planning_adoption(unit.adoption)
    assert hashlib.sha256(document(plans.get_revision(unit.revision))).digest() != hashlib.sha256(document(plans.get_adoption(unit.adoption))).digest()
    assert document(current_record['snapshot']['plan']) == document(baseline_record['snapshot']['plan'])
    snapshot = service._snapshot(unit, unit.plan, now)
    assert snapshot.baseline.plan is unit.plan
    assert value['snapshot']['baseline']['plan'] == adoption['snapshot']['plan']
    assert value['snapshot']['preferences']['near_week_weight'] == '7'
    value['snapshot']['plan']['segments'].clear()
    assert client.get('/auto_plan/api/jobs/source').json()['snapshot']['plan']['segments']

    # An unadopted changed Plan must not become the adopted baseline.
    latest = client.get('/auto_plan/api/plans/current').json()
    changed_request = deepcopy(request)
    changed_request.update(base_revision=latest['revision'], input_version=latest['input_version'])
    changed_request['operations'][0]['quantity'] = 80
    changed_request['operation_id'] = str(uuid4())
    changed_request['pending'] = True
    changed = post(client, 'save', changed_request)
    assert changed.status_code == 200, changed.text
    current = client.get('/auto_plan/api/plans/current').json()
    assert current['evaluation']['output_totals']['expected'] == 180
    unit = units[-1]
    snapshot = service._snapshot(unit, unit.plan, now)
    assert snapshot.baseline.plan is not unit.plan
    assert snapshot.baseline.plan.model_dump(mode='json') == adoption['snapshot']['plan']

    # A stale independent edit must replay against the current changed Plan,
    # while retaining the actual adopted baseline and the complete response.
    stale = deepcopy(request)
    stale.update(base_revision=latest['revision'], input_version=latest['input_version'])
    stale['operations'][0].update(segment_id='n', quantity=90, end_date='2026-10-16',
        scope={'start_date':'2026-10-16','end_date':'2026-10-16',
               'line_codes':['L'],'model_codes':['N']})
    stale_response = post(client, 'preview', stale)
    assert stale_response.status_code == 200, stale_response.text
    assert stale_response.json()['evaluation']['output_totals']['expected'] == 170
    reuse_enabled[0] = False
    try:
        fresh_stale = post(client, 'preview', stale)
        assert fresh_stale.status_code == 200, fresh_stale.text
        assert fresh_stale.json() == stale_response.json()
    finally:
        reuse_enabled[0] = True
    assert client.get('/auto_plan/api/plans/current').json() == current
    assert client.get('/auto_plan/api/plans/revisions/1').json() == plans.get_revision(1)

    # The process-lived adapter retains fresh baseline ownership, even when
    # values match. No new Oracle-only capability is attached to that adapter.
    monkeypatch.setattr(plans, 'read', normal_read)
    memory_snapshot = service._snapshot(plans, plans.plan, now)
    memory_snapshot.baseline.plan.segments.clear()
    assert plans.plan.segments
    assert plans.get_adoption(1) == adoption


def _record_plan():
    switch = {'id': 'change', 'line_code': 'L', 'from_recipe': 'R', 'to_recipe': 'S',
              'allocations': [{'date': '2026-10-15', 'order': 0, 'hours': '1', 'idle_hours': '0'}]}
    return {
        'segments': [row(quantity=1)], 'switches': [switch],
        'initial_states': [{'line_code': 'L', 'recipe_code': 'R'}],
        'ordinary_idle': [{'date': '2026-10-15', 'line_code': 'L', 'hours': '1'}],
        'current_day_witnesses': [{
            'date': '2026-10-15', 'line_code': 'L', 'basis_digest': 'basis', 'plan_digest': 'plan',
            'segments': [row('observed', quantity=1)], 'switches': [deepcopy(switch)],
            'prior_recipe_hours': [{'recipe_code': 'R', 'hours': '2'}],
            'prior_switch_hours': '1', 'prior_switches': [deepcopy(switch)],
        }],
    }


class ImmutableCursor:
    def __init__(self, records, corrupt=False):
        self.records = records
        self.corrupt = corrupt

    def execute(self, sql, **binds):
        self.value = self.records[binds['identity']]

    def fetchone(self):
        # ASCII escaping also represents legacy surrogate JSON faithfully.
        raw = json.dumps(self.value, ensure_ascii=True).encode()
        sha = hashlib.sha256(raw).hexdigest()
        return zlib.compress(raw), 'invalid' if self.corrupt else sha


def _unit(plan=None, *, corrupt=False):
    record = {'snapshot': {'plan': _record_plan() if plan is None else plan},
              'coverage': [], 'evaluation': {'kept': 'full-history'}}
    records = {number: deepcopy(record) | {'revision': number} for number in range(1, 12)}
    return OraclePlanUnit(SimpleNamespace(input_store=None), ImmutableCursor(records, corrupt),
                          (1, 1), None, None)


@pytest.mark.parametrize(('path', 'replacement'), [
    (('segments', 0, 'quantity'), True),
    (('segments', 0, 'quantity'), 1.0),
    (('segments', 0, 'hours'), '3.0'),
    (('segments', 0, 'model_code'), 'N'),
    (('segments', 0, 'order'), 1),
    (('switches', 0, 'allocations', 0, 'hours'), '2'),
    (('switches', 0, 'allocations', 0, 'idle_hours'), '1'),
    (('initial_states', 0, 'recipe_code'), None),
    (('ordinary_idle', 0, 'hours'), '2'),
    (('current_day_witnesses', 0, 'basis_digest'), 'changed'),
    (('current_day_witnesses', 0, 'plan_digest'), 'changed'),
    (('current_day_witnesses', 0, 'segments', 0, 'quantity'), 2),
    (('current_day_witnesses', 0, 'switches', 0, 'to_recipe'), 'R'),
    (('current_day_witnesses', 0, 'prior_recipe_hours', 0, 'hours'), '3'),
    (('current_day_witnesses', 0, 'prior_switch_hours'), '2'),
    (('current_day_witnesses', 0, 'prior_switches', 0, 'allocations', 0, 'hours'), '2'),
])
def test_complete_raw_field_changes_fall_back_to_original_validation(path, replacement):
    unit = _unit()
    selected = unit.get_planning_adoption(2)
    assert unit.planning_plan_value(selected) is unit.plan
    node = selected['snapshot']['plan']
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = replacement
    raw = selected['snapshot']['plan']
    assert unit.planning_plan_value(selected) is raw
    # Preserve the original validator's complete value or error diagnostics.
    try:
        expected = Plan.model_validate(raw).model_dump(mode='json')
    except ValidationError as original:
        with pytest.raises(ValidationError) as actual:
            Plan.model_validate(unit.planning_plan_value(selected))
        assert actual.value.json() == original.json()
    else:
        assert Plan.model_validate(unit.planning_plan_value(selected)).model_dump(mode='json') == expected
    assert unit.plan.segments[0].quantity == 1
    assert unit.plan.current_day_witnesses[0].prior_recipe_hours[0].hours == '2'


def test_sha_flavors_eviction_and_unrelated_values_cannot_borrow_current_plan():
    from auto_plan.access import StoreUnavailable
    with pytest.raises(StoreUnavailable):
        _unit(corrupt=True)
    unit = _unit()
    selected = unit.get_planning_revision(1)
    assert unit.planning_plan_value(selected) is unit.plan
    unrelated = deepcopy(selected)
    assert unit.planning_plan_value(unrelated) is unrelated['snapshot']['plan']
    full = unit.get_revision(1)
    assert 'evaluation' in full
    assert unit.planning_plan_value(full) is full['snapshot']['plan']
    for revision in range(2, 11):
        unit.get_planning_revision(revision)
    assert len(unit._records) == 8
    assert unit.planning_plan_value(selected) is selected['snapshot']['plan']
    reloaded = unit.get_planning_revision(1)
    assert reloaded is not selected
    assert unit.planning_plan_value(reloaded) is unit.plan
    assert unit.get_revision(1)['evaluation'] == {'kept': 'full-history'}
    selected = unit.get_planning_revision(1)
    selected['snapshot']['plan']['segments'][0]['quantity'] = True
    assert unit.planning_plan_value(selected) is selected['snapshot']['plan']
    with pytest.raises(ValidationError):
        Plan.model_validate(unit.planning_plan_value(selected))


def test_unsupported_canonical_text_and_extra_fields_keep_original_validation():
    plan = _record_plan()
    plan['current_day_witnesses'][0]['basis_digest'] = '\ud800'
    unit = _unit(plan)
    selected = unit.get_planning_revision(1)
    assert unit.planning_plan_value(selected) is selected['snapshot']['plan']
    assert Plan.model_validate(unit.planning_plan_value(selected)).current_day_witnesses[0].basis_digest == '\ud800'
    unit = _unit()
    selected = unit.get_planning_adoption(2)
    selected['snapshot']['plan']['unknown_field'] = []
    assert unit.planning_plan_value(selected) is selected['snapshot']['plan']
    with pytest.raises(ValidationError):
        Plan.model_validate(unit.planning_plan_value(selected))


def test_typed_equivalent_omitted_defaults_and_list_reordering_still_validate_raw_values():
    unit = _unit(Plan().model_dump(mode='json'))
    selected = unit.get_planning_adoption(2)
    del selected['snapshot']['plan']['current_day_witnesses']
    raw = selected['snapshot']['plan']
    assert Plan.model_validate(raw) == unit.plan
    assert unit.planning_plan_value(selected) is raw
    plan = _record_plan()
    plan['segments'].append(row('second', model='N', quantity=2))
    unit = _unit(plan)
    selected = unit.get_planning_adoption(2)
    selected['snapshot']['plan']['segments'].reverse()
    raw = selected['snapshot']['plan']
    assert unit.planning_plan_value(selected) is raw
    assert [segment.id for segment in Plan.model_validate(raw).segments] == ['second', 'a']
    assert [segment.id for segment in unit.plan.segments] == ['a', 'second']


def test_typed_current_plan_and_canonical_proof_are_owned_by_one_unit():
    first, second = _unit(), _unit()
    assert first.plan is not second.plan
    first.plan.current_day_witnesses[0].segments.clear()
    first.get_planning_revision(1)['snapshot']['plan']['segments'].clear()
    selected = second.get_planning_revision(1)
    assert second.planning_plan_value(selected) is second.plan
    assert second.plan.segments[0].quantity == 1
    assert second.plan.current_day_witnesses[0].segments[0].quantity == 1
