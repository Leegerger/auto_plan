"""External-server PLAN_TEST adapter. Mounted only by controlled validation.

The production image/entrypoint has no namespace or clock bypass. This explicit
adapter supplies the already agreed database and clock testing boundaries.
"""
import os,re,logging,sys
from datetime import datetime
from pathlib import Path
import uvicorn
from auto_plan.app import create_app
from auto_plan.oracle_access import OracleAccessStore
from auto_plan.oracle_inputs import OracleInputStore
from auto_plan.oracle_plans import OraclePlanStore
from auto_plan.oracle_demands import OracleDemandStore
from auto_plan.schema import manifest,verify_schema,MIGRATIONS
from auto_plan.job_api import configure_jobs
from auto_plan.settings import Settings
from auto_plan.serve import Server
from tests.oracle_namespace import NamespacePool

logging.basicConfig(level=logging.INFO,stream=sys.stdout,format='%(levelname)s %(name)s %(message)s')
settings=Settings()
if settings.oracle_user.upper()!='PLAN_TEST' or not settings.test_auth_enabled:
    raise SystemExit('Explicit PLAN_TEST test authentication required')
prefix=os.environ['AUTO_PLAN_TEST_NAMESPACE']
if not re.fullmatch('AP_TEST_[A-F0-9]{6}',prefix):raise SystemExit('Disposable namespace required')
identifiers=[]
for path in MIGRATIONS.glob('*.sql'):
    identifiers.extend(re.findall(r'CREATE (?:OR REPLACE )?(?:TABLE|INDEX|TRIGGER) (AP_[A-Z0-9_]+)',path.read_text()))
access=OracleAccessStore.connect(settings)
access.pool=NamespacePool(access.pool,prefix,identifiers=identifiers)
verify_schema(access.pool)
plans=OraclePlanStore(access.pool)
as_of=datetime.fromisoformat(os.environ['AUTO_PLAN_TEST_AS_OF'])
if as_of.tzinfo is None:raise SystemExit('Aware fixed test time required')
app=create_app(settings,store=access,input_store=OracleInputStore(access.pool),plan_store=plans,
    demand_store=OracleDemandStore(plans),clock=lambda:as_of)
configure_jobs(app)
try:
    Server(uvicorn.Config(app,host='0.0.0.0',port=8000,log_level='info',access_log=False,log_config=None,
        ssl_keyfile='/test-tls/key.pem',ssl_certfile='/test-tls/cert.pem')).run()
finally:
    access.pool.pool.close(force=True)
