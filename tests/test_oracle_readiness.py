"""Real disposable PLAN_TEST failures observed at the service readiness API."""
import os
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from auto_plan.app import create_app
from auto_plan.settings import Settings
from auto_plan.oracle_access import OracleAccessStore
from auto_plan.oracle_inputs import OracleInputStore
from auto_plan.oracle_plans import OraclePlanStore
from tests.oracle_namespace import disposable_business_namespace
from tests.support import CompanyAuthentication

pytestmark=[pytest.mark.oracle,pytest.mark.skipif(os.getenv('AUTO_PLAN_TEST_ORACLE')!='1',reason='Opt-in PLAN_TEST required')]


def test_real_corrupted_input_head_refuses_public_reads_and_writes():
    settings=Settings();assert settings.oracle_user.upper()=='PLAN_TEST'
    access=OracleAccessStore.connect(settings)
    try:
        with disposable_business_namespace(access.pool,'AP_TEST_'+uuid4().hex[:6].upper()) as pool:
            with pool.acquire() as connection:
                with connection.cursor() as cursor: cursor.execute('UPDATE AP_INPUT_HEAD SET CURRENT_VERSION=999 WHERE ID=1')
                connection.commit()
            app=create_app(settings,store=access,input_store=OracleInputStore(pool),plan_store=OraclePlanStore(pool),authenticator=CompanyAuthentication())
            with TestClient(app,base_url=settings.public_origin) as client:
                response=client.get('/auto_plan/api/ready')
                assert response.status_code==503
                assert response.json()['reason_code']=='input_integrity'
                assert client.get('/auto_plan/api/inputs').status_code==503
    finally: access.close()


def test_real_orphan_context_reference_refuses_public_service():
    from datetime import datetime,timezone
    from auto_plan.oracle_plans import encode,document
    import hashlib,oracledb
    settings=Settings();assert settings.oracle_user.upper()=='PLAN_TEST'
    access=OracleAccessStore.connect(settings)
    try:
        with disposable_business_namespace(access.pool,'AP_TEST_'+uuid4().hex[:6].upper()) as pool:
            inputs=OracleInputStore(pool)
            # Set up a valid immutable input/context with a nonexistent lineage source.
            from auto_plan.input_models import InputSnapshot
            snapshot=InputSnapshot(input_version=1).model_dump(mode='json')
            context={'lineage':{'resets':{'2026-10-12':{'source_adoption':999}}}}
            with pool.acquire() as connection:
                with connection.cursor() as cursor:
                    cursor.setinputsizes(blob=oracledb.DB_TYPE_BLOB)
                    cursor.execute('INSERT INTO AP_INPUT_REVISION (INPUT_VERSION,SNAPSHOT_BLOB,SHA256,ACTOR_ID,RECORDED_AT) VALUES (1,:blob,:sha,\'T38\',:now)',blob=encode(snapshot),sha=hashlib.sha256(document(snapshot)).hexdigest(),now=datetime.now(timezone.utc))
                    cursor.execute('UPDATE AP_INPUT_HEAD SET CURRENT_VERSION=1 WHERE ID=1')
                    cursor.execute('INSERT INTO AP_PLAN_CONTEXT (INPUT_VERSION,CONTEXT_BLOB,SHA256,ACTOR_ID,RECORDED_AT) VALUES (1,:blob,:sha,\'T38\',:now)',blob=encode(context),sha=hashlib.sha256(document(context)).hexdigest(),now=datetime.now(timezone.utc))
                connection.commit()
            with TestClient(create_app(settings,store=access,input_store=inputs,plan_store=OraclePlanStore(pool),authenticator=CompanyAuthentication()),base_url=settings.public_origin) as client:
                response=client.get('/auto_plan/api/ready')
                assert response.status_code==503
                assert response.json()['reason_code']=='plan_integrity'
    finally: access.close()
