"""Operator migration protocol over actual disposable PLAN_TEST objects."""
import os,re
from contextlib import contextmanager
from uuid import uuid4
import pytest
import oracledb
from auto_plan.migrate import operate
from auto_plan.schema import verify_schema,manifest,MIGRATIONS
from auto_plan.oracle_access import OracleAccessStore
from auto_plan.settings import Settings
from tests.oracle_namespace import NamespacePool

pytestmark=[pytest.mark.oracle,pytest.mark.skipif(os.getenv('AUTO_PLAN_TEST_ORACLE')!='1',reason='Opt-in PLAN_TEST required')]
META=dict(recovery_point='disposable-test-no-business-data',operator_id='automated-PLAN_TEST',release_id='0.1.0-spec23',source_revision='test-fixture')


@contextmanager
def installation():
    settings=Settings();assert settings.oracle_user.upper()=='PLAN_TEST'
    access=OracleAccessStore.connect(settings)
    documents=[p.read_text() for p in sorted(MIGRATIONS.glob('*.sql'))]
    objects=[(kind,name) for document in documents for kind,name in re.findall(r'CREATE (?:OR REPLACE )?(TABLE|INDEX|TRIGGER) (AP_[A-Z0-9_]+)',document)]
    pool=NamespacePool(access.pool,'AP_TEST_'+uuid4().hex[:6].upper(),identifiers=[name for _,name in objects])
    try: yield pool
    finally:
        with access.pool.acquire() as connection:
            with connection.cursor() as cursor:
                for kind,name in reversed(objects):
                    if kind!='TABLE':continue
                    try:cursor.execute('DROP TABLE '+pool.names[name]+' CASCADE CONSTRAINTS PURGE')
                    except oracledb.Error as error:
                        if error.args[0].code!=942:raise
            connection.commit()
        access.close()


def test_fresh_operator_migrations_are_replay_safe_and_checksum_tampering_fails_closed():
    with installation() as pool:
        operate(pool,None,'PLAN_TEST',metadata=META,bootstrap=True)
        for _,filename,_ in manifest():operate(pool,MIGRATIONS/filename,'PLAN_TEST',metadata=META)
        assert verify_schema(pool)
        # Repeating an acknowledged complete migration is an explicit no-op.
        operate(pool,MIGRATIONS/'005_jobs.sql','PLAN_TEST',metadata=META)
        with pool.acquire() as connection:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE AP_SCHEMA_MIGRATION SET SHA256=:sha WHERE VERSION_NO=5",sha='0'*64)
            connection.commit()
        with pytest.raises(ValueError): verify_schema(pool)
        with pytest.raises(ValueError):operate(pool,MIGRATIONS/'005_jobs.sql','PLAN_TEST',metadata=META)


def test_partial_ddl_failure_is_recorded_and_refuses_blind_retry():
    with installation() as pool:
        operate(pool,None,'PLAN_TEST',metadata=META,bootstrap=True)
        with pool.acquire() as connection:
            with connection.cursor() as cursor:
                cursor.execute('CREATE TABLE AP_ACCESS_HEAD (ID NUMBER PRIMARY KEY)')
            connection.commit()
        with pytest.raises(oracledb.Error):operate(pool,MIGRATIONS/'001_access.sql','PLAN_TEST',metadata=META)
        with pytest.raises(ValueError):operate(pool,MIGRATIONS/'001_access.sql','PLAN_TEST',metadata=META)
        with pytest.raises(ValueError):verify_schema(pool)


def test_explicit_inspected_baseline_requires_complete_objects_and_enabled_triggers():
    from auto_plan.migrate import statements
    with installation() as pool:
        operate(pool,None,'PLAN_TEST',metadata=META,bootstrap=True)
        with pytest.raises(Exception):operate(pool,None,'PLAN_TEST',metadata=META,baseline_through=5)
        with pool.acquire() as connection:
            with connection.cursor() as cursor:
                for _,filename,_ in manifest():
                    for statement in statements((MIGRATIONS/filename).read_text()):cursor.execute(statement)
            connection.commit()
        operate(pool,None,'PLAN_TEST',metadata=META,baseline_through=5)
        assert verify_schema(pool)
