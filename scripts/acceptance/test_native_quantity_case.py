"""Local fixture/raw-progress controls; these never start an application or job."""
import hashlib
import json
from types import SimpleNamespace
import pytest
from scripts.acceptance.native_quantity_case import quantity_inputs, quantity_command, native_quantity_stage, quantity_objective, PublicJobProof, collect_job_cleanup


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


@pytest.mark.parametrize('ledger_bytes', [None, b'{malformed ledger\n'])
@pytest.mark.parametrize('raw,status,error_type', [(b'{"detail":"source unavailable"}',503,AssertionError), (b'{malformed source',200,json.JSONDecodeError)])
def test_pre_spawn_cleanup_preserves_original_api_or_parse_error_and_raw_proof(tmp_path, raw, status, error_type, ledger_bytes):
    proof = PublicJobProof(tmp_path/'raw')
    observer = SimpleNamespace(path=tmp_path/'no-pre-spawn-ledger.ndjson', processes={})
    if ledger_bytes is not None:
        observer.path.write_bytes(ledger_bytes)
    original, closure = [], []
    def before_claim():
        try:
            proof.capture('source-before',SimpleNamespace(content=raw,status_code=status))
            assert status == 200, 'original source503'
        except BaseException as error:
            original.append(error)
            raise
        finally:
            closure.append(collect_job_cleanup(observer))
    with pytest.raises(error_type) as caught:
        before_claim()
    assert caught.value is original[0]
    row = json.loads((proof.directory/'index.ndjson').read_text())
    assert (proof.directory/row['body']['path']).read_bytes() == raw
    assert row['status'] == status
    records, cleanup = closure[0]
    assert records == [] and cleanup['ledger_status'] == ('absent' if ledger_bytes is None else 'unreadable')
    assert cleanup['job_acquired'] is (False if ledger_bytes is None else None) and cleanup['all_closed'] is None


def test_actual_recorded_owner_requires_physical_closure_and_early_unknown_is_not_no_job(tmp_path, monkeypatch):
    import scripts.acceptance.job_lifecycle_observer as lifecycle
    import scripts.acceptance.native_quantity_case as case
    observer = SimpleNamespace(path=tmp_path/'ledger.ndjson',processes={})
    observer.path.write_text('{"kind":"spawn_unknown"}\n')
    records, cleanup = collect_job_cleanup(observer)
    assert cleanup['job_acquired'] is True and cleanup['all_closed'] is None
    owner = {'pid':123,'start':'456','pgid':123}
    observer.processes = {'J':(SimpleNamespace(child=SimpleNamespace(poll=lambda:None)),owner)}
    monkeypatch.setattr(lifecycle,'identity',lambda pid:owner)
    monkeypatch.setattr(case.os,'killpg',lambda pgid,sig:None)
    _, cleanup = collect_job_cleanup(observer)
    assert cleanup['job_acquired'] is True and cleanup['all_closed'] is False
    observer.path.write_text('')
    observer.processes['J'][0].child.poll = lambda:0
    monkeypatch.setattr(lifecycle,'identity',lambda pid:None)
    def gone(pgid,sig):
        raise ProcessLookupError()
    monkeypatch.setattr(case.os,'killpg',gone)
    _, cleanup = collect_job_cleanup(observer)
    assert cleanup['all_closed'] is True


@pytest.mark.parametrize('ledger_bytes',[b'null\n',b'[]\n',b'"text"\n',b'1\n',b'{}\n',b'{"kind": []}\n'])
def test_nonrecord_ledger_preserves_source_error_and_reports_unknown(tmp_path,ledger_bytes):
    observer=SimpleNamespace(path=tmp_path/'ledger.ndjson',processes={})
    observer.path.write_bytes(ledger_bytes)
    original=AssertionError('ORIGINAL_SOURCE_503')
    closure=[]
    with pytest.raises(AssertionError,match='ORIGINAL_SOURCE_503') as caught:
        try:
            raise original
        finally:
            closure.append(collect_job_cleanup(observer))
    assert caught.value is original
    records,cleanup=closure[0]
    assert records==[] and cleanup['ledger_status']=='unreadable'
    assert cleanup['job_acquired'] is None and cleanup['all_closed'] is None


@pytest.mark.parametrize('bad_tail',[b'null\n',b'{malformed\n'])
def test_unreadable_tail_never_loses_known_spawn_unknown_prefix(tmp_path,bad_tail):
    observer=SimpleNamespace(path=tmp_path/'ledger.ndjson',processes={})
    observer.path.write_bytes(b'{"kind":"spawn_unknown"}\n'+bad_tail)
    records,cleanup=collect_job_cleanup(observer)
    assert records==[] and cleanup['ledger_status']=='unreadable'
    assert cleanup['job_acquired'] is True and cleanup['all_closed'] is None


def test_complete_normal_ledger_is_returned_exactly(tmp_path):
    observer=SimpleNamespace(path=tmp_path/'ledger.ndjson',processes={})
    expected=[{'kind':'read','nonce':'ORIGINAL','process_alive':False},{'kind':'progress','stage':'ORIGINAL'}]
    observer.path.write_text(''.join(json.dumps(row)+'\n' for row in expected))
    records,cleanup=collect_job_cleanup(observer)
    assert records==expected and cleanup['ledger_status']=='complete'
    assert cleanup['job_acquired'] is False and cleanup['all_closed'] is None


def test_unknown_record_kind_and_malformed_ledger_keep_owned_acquisition(tmp_path,monkeypatch):
    import scripts.acceptance.job_lifecycle_observer as lifecycle
    import scripts.acceptance.native_quantity_case as case
    owner={'pid':123,'start':'456','pgid':123}
    observer=SimpleNamespace(path=tmp_path/'ledger.ndjson',processes={'J':(SimpleNamespace(child=SimpleNamespace(poll=lambda:None)),owner)})
    observer.path.write_text('{"kind":"UNRECOGNIZED"}\n')
    monkeypatch.setattr(lifecycle,'identity',lambda pid:owner)
    monkeypatch.setattr(case.os,'killpg',lambda pgid,sig:None)
    records,cleanup=collect_job_cleanup(observer)
    assert records==[] and cleanup['ledger_status']=='unreadable'
    assert cleanup['job_acquired'] is True and cleanup['all_closed'] is False
