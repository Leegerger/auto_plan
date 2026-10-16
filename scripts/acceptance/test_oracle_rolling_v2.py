"""One actual Oracle rolling lifecycle in an entirely disposable namespace."""
from datetime import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from auto_plan.app import create_app
from auto_plan.migrate import statements
from auto_plan.oracle_access import OracleAccessStore
from auto_plan.oracle_demands import OracleDemandStore
from auto_plan.oracle_plans import OraclePlanStore
from auto_plan.settings import Settings
from scripts.acceptance.assertions import assert_frozen_snapshot
from scripts.acceptance.compare import assert_reference, assert_protected
from scripts.acceptance.rolling_v2 import OutcomeUnknown, load, prepare_public
from tests.oracle_namespace import BUSINESS_IDENTIFIERS, NamespacePool, disposable_business_namespace
from tests.support import CompanyAuthentication

pytestmark = [pytest.mark.oracle, pytest.mark.skipif(
    os.getenv('AUTO_PLAN_TEST_ORACLE') != '1', reason='Opt-in disposable PLAN_TEST only')]
ROOT = Path(__file__).resolve().parents[2]
ACCESS = ('AP_ACCESS_HEAD', 'AP_AUTHORIZATION', 'AP_SESSION', 'AP_SESSION_USER',
          'AP_AUTH_AUDIT', 'AP_AUTH_AUDIT_IMMUTABLE')


def test_real_r10_prior_adoption_restart_and_rolling_original_uuid(tmp_path):
    fixtures = ROOT / 'docs/acceptance/rolling-v2/frozen'
    scenario = next(row for row in json.loads((fixtures/'manifest.json').read_text())['scenarios']
                    if row['id'] == 'r10-rolling-v2')
    source, prior, reference = (load(fixtures, scenario[name]) for name in ('source', 'prior_source', 'reference'))
    settings = Settings(initial_admin_id='ACCEPTANCE0', oracle_pool_max=2)
    assert settings.oracle_user.upper() == 'PLAN_TEST'
    control = OracleAccessStore.connect(settings)
    prefix = 'AP_TEST_' + uuid4().hex[:6].upper()
    metadata = {'namespace': prefix, 'kind': 'actual Oracle public lifecycle; synthetic company verification; no timing acceptance',
                'phases': [], 'phase_pools_closed': [], 'lost_demand_uuid': None}
    output = tmp_path/'prepared'
    wire = tmp_path/'wire'; wire.mkdir()
    def persist():
        (tmp_path/'namespace-cleanup.json').write_text(json.dumps(metadata, indent=2)+'\n')
    def objects():
        with control.pool.acquire() as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT OBJECT_NAME,OBJECT_TYPE FROM USER_OBJECTS WHERE SUBSTR(OBJECT_NAME,1,:n)=:prefix ORDER BY OBJECT_NAME',
                               n=len(prefix), prefix=prefix)
                return [list(row) for row in cursor]
    config = {'scenario': scenario['id'], 'target': {'kind': 'PLAN_TEST', 'namespace': prefix,
              'disposable': True, 'production': False}}
    try:
        metadata['objects_before'] = objects(); assert metadata['objects_before'] == []
        persist()
        with disposable_business_namespace(control.pool, prefix):
            schema = NamespacePool(control.pool, prefix, BUSINESS_IDENTIFIERS + ACCESS)
            try:
                with schema.acquire() as connection:
                    with connection.cursor() as cursor:
                        for statement in statements((ROOT/'migrations/001_access.sql').read_text()):
                            cursor.execute(statement)
                    connection.commit()
                metadata['objects_installed'] = objects(); persist()
                for phase, clock in [('initial', prior['time']['as_of']), ('rolling', source['time']['as_of'])]:
                    # Each application restart has a fresh physical pool/store.
                    # Only the DDL/cleanup control pool survives both phases.
                    physical = OracleAccessStore.connect(settings)
                    pool = NamespacePool(physical.pool, prefix, BUSINESS_IDENTIFIERS + ACCESS)
                    access, plans = OracleAccessStore(pool), OraclePlanStore(pool)
                    app = create_app(settings, store=access, input_store=plans.input_store, plan_store=plans,
                                     demand_store=OracleDemandStore(plans), authenticator=CompanyAuthentication(),
                                     clock=lambda fixed=datetime.fromisoformat(clock): fixed)
                    metadata['phases'].append({'phase': phase, 'clock': clock, 'status': 'started'})
                    persist()
                    try:
                        with TestClient(app, base_url=settings.public_origin) as client:
                            client.headers.update({'Origin': settings.public_origin, 'X-Auto-Plan-Request': '1'})
                            assert client.get('/auto_plan/api/ready').status_code == 200
                            assert client.post('/auto_plan/api/auth/login', json={
                                'username': 'ACCEPTANCE0', 'password': 'company-secret'}).status_code == 200
                            if phase == 'initial':
                                response = client.put('/auto_plan/api/admin/authorizations/ACCEPTANCE0',
                                                      json={'roles': ['administrator', 'editor']})
                                assert response.status_code == 200
                            def call(path, payload=None):
                                response = (client.get('/auto_plan/api'+path) if payload is None else
                                            client.post('/auto_plan/api'+path, json=payload))
                                raw = response.content
                                record = {'phase': phase, 'path': path, 'status': response.status_code,
                                          'request': payload, 'response_sha256': hashlib.sha256(raw).hexdigest()}
                                index = len(list(wire.glob('*.json')))
                                (wire/f'{index:03d}.json').write_text(json.dumps(record, ensure_ascii=False))
                                (wire/f'{index:03d}.response.json.gz').write_bytes(gzip.compress(raw, mtime=0))
                                assert response.status_code == 200, response.text[:1000]
                                value = response.json()
                                if path == '/demands/apply' and metadata['lost_demand_uuid'] is None:
                                    metadata['lost_demand_uuid'] = payload['operation_id']; persist()
                                    raise OutcomeUnknown
                                return value
                            result = prepare_public(call, fixtures, config, output, phase=phase)
                            metadata['phases'][-1]['status'] = result['status']; persist()
                            if phase == 'initial':
                                assert result['status'] == 'awaiting_clock_transition'
                            else:
                                assert result['status'] == 'pass'
                                assert result['actual_tokens'] == {'source_revision': 3, 'input_version': 3, 'latest_adoption': 1}
                                demand = result['operations'][2]
                                assert demand['operation_id'] == metadata['lost_demand_uuid']
                                assert demand['unknown_outcome_reconciled'] and demand['result'] == demand['verification']
                                current = call('/plans/current')
                                assert current['evaluation']['output_totals']['expected'] == 392000
                                assert current['evaluation']['output_totals']['gap'] == 28000
                                assert len(current['evaluation']['weekly_results']) == 600
                                snapshot = json.loads((output/'state.json').read_text())['snapshot']
                                assert_frozen_snapshot(snapshot, source)
                                independent = call('/plans/evaluate', {'snapshot': snapshot})
                                assert independent == current['evaluation']
                                checked = call('/plans/evaluate', {'snapshot': reference})
                                assert_reference(checked, scenario)
                                assert checked['output_totals']['expected'] == checked['output_totals']['on_time'] == 420000
                                assert checked['output_totals']['gap'] == 0 and len(checked['weekly_results']) == 600
                                assert_protected(source, reference['plan'])
                                assert plans.check_consistency() is True
                                assert call('/plans/current') == current
                            assert client.post('/auto_plan/api/auth/logout').status_code == 200
                    finally:
                        physical.close()
                        metadata['phase_pools_closed'].append(phase); persist()
            finally:
                # Only this namespace's access tables were added above. Table
                # drops remove their own indexes and immutable audit trigger.
                with schema.acquire() as connection:
                    with connection.cursor() as cursor:
                        for table in ('AP_SESSION', 'AP_AUTH_AUDIT', 'AP_AUTHORIZATION', 'AP_ACCESS_HEAD'):
                            cursor.execute('DROP TABLE '+schema.names[table]+' CASCADE CONSTRAINTS PURGE')
                    connection.commit()
        metadata['objects_after'] = objects(); assert metadata['objects_after'] == []
        metadata['status'] = 'pass'; persist()
    finally:
        control.close()
        metadata['control_pool_closed'] = True; persist()
