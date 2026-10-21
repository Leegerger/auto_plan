"""Opt-in maintenance grant checks on disposable, namespaced Oracle tables."""
from datetime import datetime, timezone
import json
import os

import pytest

from auto_plan.migrate import operate
from auto_plan.oracle_access import OracleAccessStore
from auto_plan.schema import MIGRATIONS
from auto_plan.test_env import grant_admin
from tests.test_oracle_migrations import installation, META


pytestmark = [pytest.mark.oracle, pytest.mark.skipif(
    os.getenv('AUTO_PLAN_TEST_ORACLE') != '1', reason='Opt-in PLAN_TEST credentials required',
)]


def test_admin_grant_preserves_roles_and_rolls_back_when_audit_fails(monkeypatch):
    with installation() as pool:
        operate(pool, None, 'PLAN_TEST', metadata=META, bootstrap=True)
        operate(pool, MIGRATIONS / '001_access.sql', 'PLAN_TEST', metadata=META)
        store = OracleAccessStore(pool)
        now = datetime.now(timezone.utc)
        with store.transaction() as cursor:
            cursor.execute("INSERT INTO AP_AUTHORIZATION (USER_ID,VIEWER,EDITOR,ADMINISTRATOR,ACTIVE) VALUES ('T24SETUPADMIN',1,1,0,0)")
        with monkeypatch.context() as patch:
            def failed_audit(*args):
                raise RuntimeError('simulated audit failure')
            patch.setattr(store, '_audit', failed_audit)
            with pytest.raises(RuntimeError, match='simulated audit failure'):
                grant_admin(store, 'T24SETUPADMIN', now)
        with store.transaction() as cursor:
            cursor.execute("SELECT VIEWER,EDITOR,ADMINISTRATOR,ACTIVE FROM AP_AUTHORIZATION WHERE USER_ID='T24SETUPADMIN'")
            assert cursor.fetchone() == (1, 1, 0, 0)
            cursor.execute('SELECT COUNT(*) FROM AP_AUTH_AUDIT')
            assert cursor.fetchone()[0] == 0
        grant_admin(store, 'T24SETUPADMIN', now)
        with store.transaction() as cursor:
            cursor.execute("SELECT VIEWER,EDITOR,ADMINISTRATOR,ACTIVE FROM AP_AUTHORIZATION WHERE USER_ID='T24SETUPADMIN'")
            assert cursor.fetchone() == (1, 1, 1, 1)
            cursor.execute('SELECT ACTOR_ID,USER_ID,ACTION,ROLES_JSON,ACTIVE FROM AP_AUTH_AUDIT')
            actor, user, action, roles, active = cursor.fetchone()
            assert (actor, user, action, active) == ('PLAN_TEST_SETUP', 'T24SETUPADMIN', 'authorize', 1)
            assert set(json.loads(roles)) == {'viewer', 'editor', 'administrator'}
