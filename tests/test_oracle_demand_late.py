"""Explicit late mode persists through the existing real PLAN_TEST boundary."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from auto_plan.app import create_app
from auto_plan.oracle_access import OracleAccessStore
from auto_plan.oracle_inputs import OracleInputStore
from auto_plan.oracle_plans import OraclePlanStore
from auto_plan.oracle_demands import OracleDemandStore
from auto_plan.settings import Settings
from tests.oracle_namespace import disposable_business_namespace
from tests.support import CompanyAuthentication
from tests.test_initialization import initial_payload
from tests.test_execution_reset import current_command, head, preview
from tests.test_plan_transactions import post
from tests.test_demand_trials import trial
from tests.test_output_preview import confirmation
from tests.line_state_support import confirm_states
from tests.test_inputs import batch

pytestmark = [pytest.mark.oracle, pytest.mark.skipif(os.getenv('AUTO_PLAN_TEST_ORACLE') != '1', reason='Opt-in disposable PLAN_TEST objects only')]


def test_real_late_mode_save_retry_confirmation_restore_and_fresh_reopen():
    settings = Settings(initial_admin_id='T24ADMIN')
    assert settings.oracle_user.upper() == 'PLAN_TEST'
    access = OracleAccessStore.connect(settings)
    proof = Path('/tmp/auto-plan-spec49/oracle61-evidence')
    proof.mkdir(exist_ok=True)
    prefix = 'AP_TEST_' + uuid4().hex[:6].upper()
    try:
        with disposable_business_namespace(access.pool,prefix) as namespace:
            def app():
                plans = OraclePlanStore(namespace)
                return create_app(settings,store=access,input_store=OracleInputStore(namespace),plan_store=plans,
                    demand_store=OracleDemandStore(plans),authenticator=CompanyAuthentication(),
                    clock=lambda:datetime(2026,10,14,tzinfo=timezone.utc))
            with TestClient(app(),base_url=settings.public_origin) as client:
                client.headers.update({'Origin':settings.public_origin,'X-Auto-Plan-Request':'1'})
                assert client.post('/auto_plan/api/auth/login',json={'username':'T24ADMIN','password':'company-secret'}).status_code == 200
                assert client.put('/auto_plan/api/admin/authorizations/T33EDITOR',json={'roles':['editor']}).status_code == 200
                assert client.post('/auto_plan/api/auth/login',json={'username':'T33EDITOR','password':'company-secret'}).status_code == 200
                initial = initial_payload()
                initial.update(operation_id=str(uuid4()),segments=[])
                initial['reset']['models'][0]['net_balance'] = 0
                initial['changes']['capacities'][0]['pieces'] = 600
                seeded = client.post('/auto_plan/api/initialization/apply',json=initial)
                assert seeded.status_code == 200, seeded.text
                checked = trial(client,mode='late',target_output=1000,operations=[confirm_states()],start_date='2026-10-18',end_date='2026-10-19',
                    scope={'start_date':'2026-10-18','end_date':'2026-10-19','line_codes':['L'],'model_codes':['M']})
                assert checked.status_code == 200, checked.text
                result = checked.json()
                assert (result['scheduled_on_time_output'],result['scheduled_late_output'],result['valid_late_output'],result['remaining_output_target']) == (600,400,0,0)
                command = current_command(client,confirm_states(),result['operation'])
                saved = post(client,'save',command)
                assert saved.status_code == 200, saved.text
                assert post(client,'save',command).json() == saved.json()
                assert post(client,'verify',{'operation_id':command['operation_id']}).json() == saved.json()
                source_id = saved.json()['revision']
                source = client.get(f'/auto_plan/api/plans/revisions/{source_id}').json()
                assert source['operations'][-1]['mode'] == 'late'
                assert post(client,'adopt',current_command(client)).status_code == 422
                changed = batch(client,{'confirmations':[confirmation('shipping','2026-10-12',400,'2026-10-19')]})
                assert changed.status_code == 200, changed.text
                adopted = post(client,'adopt',current_command(client))
                assert adopted.status_code == 200, adopted.text
                restore = {'kind':'restore_history','source':{'kind':'revision','id':source_id}}
                restored = preview(client,restore)
                assert restored.status_code == 200, restored.text
                row = restored.json()['evaluation']['weekly_results'][0]
                assert (row['on_time'],row['on_time_rate'],row['late'],row['gap']) == (600,'3/5',400,0)
                assert post(client,'save',current_command(client,restore)).status_code == 200
                expected = head(client)
                goal, = expected['lineage']['demand_goals'].values()
                assert goal['remaining_output_target'] == 0
                assert next(iter(goal['accepted_actions'].values()))['mode'] == 'late'
                assert client.get(f'/auto_plan/api/plans/revisions/{source_id}').json() == source
                for name,value in [('trial',result),('save_request',command),('source_history',source),('expected_reopen',expected)]:
                    (proof/f'{name}.json').write_text(json.dumps(value,ensure_ascii=False,sort_keys=True))
            with TestClient(app(),base_url=settings.public_origin) as reopened:
                reopened.headers.update({'Origin':settings.public_origin,'X-Auto-Plan-Request':'1'})
                assert reopened.post('/auto_plan/api/auth/login',json={'username':'T33EDITOR','password':'company-secret'}).status_code == 200
                assert head(reopened) == expected
                assert reopened.get(f'/auto_plan/api/plans/revisions/{source_id}').json() == source
        (proof/'cleanup.json').write_text(json.dumps({'namespace':prefix,'business_namespace_dropped':True,'canonical_business_tables_modified':False}))
    finally:
        access.close()
