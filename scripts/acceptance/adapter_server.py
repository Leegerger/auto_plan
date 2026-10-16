"""Disposable local preparation server with test adapters for persistence and authentication.

Never use this module as a deployed entrypoint. It cannot connect to Oracle.
"""
import argparse
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import local
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.acceptance.validate import load
from tests.plan_support import MemoryPlanStore


class BrowserPlanStore(MemoryPlanStore):
    """Pin read inputs once while leaving input writers on their own lock."""
    def __init__(self, inputs):
        self._read_inputs = local()
        super().__init__(inputs)

    @property
    def inputs(self):
        pinned = getattr(self._read_inputs, 'value', None)
        return pinned if pinned is not None else super().inputs

    def read(self, actor, action):
        with self.lock:
            if getattr(self._read_inputs, 'value', None) is not None:
                return action(self)
            self._read_inputs.value = self.input_store.read()
            try:
                return action(self)
            finally:
                self._read_inputs.value = None


def application(fixtures, scenario_id, port):
    from auto_plan.app import create_app
    from auto_plan.input_models import InputSnapshot
    from auto_plan.plan_models import Plan, EvaluationSnapshot
    from auto_plan.rules import evaluate
    from auto_plan.plans import PlanningContext
    from auto_plan.settings import Settings
    from auto_plan.job_process import JobController
    from tests.input_support import MemoryInputStore
    from tests.jobs_support import MemoryJobStore
    from tests.demand_support import MemoryDemandStore
    from tests.support import MemoryAccessStore, CompanyAuthentication
    scenario = next(row for row in json.loads((fixtures / 'manifest.json').read_text())['scenarios'] if row['id'] == scenario_id)
    source = load(fixtures, scenario['source'])
    empty = scenario.get('adapter_preparation') == {'kind': 'never_initialized_inputs'}
    inputs, access = MemoryInputStore(), MemoryAccessStore()
    maintained = deepcopy(source['inputs'])
    if empty:
        maintained['business']['demands'] = []
    inputs.versions[1] = InputSnapshot.model_validate(maintained); inputs.version = 1
    plans = BrowserPlanStore(inputs)
    if not empty:
        plans.plan = Plan.model_validate(source['plan'])
        plans.context = PlanningContext(execution=source['execution'], reset=source['reset'], locks=source['locks'], coverage=source['baseline']['coverage'])
        plans.revision = plans.adoption = 1
        plans.revisions[1] = {'snapshot': deepcopy(source), 'coverage': source['baseline']['coverage'], 'lineage': {}, 'operations': [], 'effects': [],
                              'pending': False, 'kind': 'adopt', 'actor': 'ACCEPTANCE0', 'revision': 1, 'recorded_at': source['time']['as_of']}
        plans.revisions[1].update(input_version=1,latest_adoption=1,
            evaluation=evaluate(EvaluationSnapshot.model_validate(source)).model_dump(mode='json'))
        plans.adoptions[1] = deepcopy(plans.revisions[1])
    for number in range(5):
        name = f'ACCEPTANCE{number}'
        access.users[name] = {'user_id': name, 'roles': ['editor'], 'active': True}
    for number in range(20):
        name = f'MATRIX{number:02d}'
        roles = [['editor'], ['viewer'], ['administrator'], ['editor', 'administrator']][number % 4]
        access.users[name] = {'user_id': name, 'roles': roles if number != 18 else [], 'active': number != 19}
    clock = lambda: datetime.fromisoformat(source['time']['as_of'])
    jobs = MemoryJobStore(plans, access)
    controller = JobController(jobs, workers=1, clock=clock)
    return create_app(Settings(public_origin=f'https://localhost:{port}', base_path='/planning', frontend_dist=ROOT / 'frontend/dist'),
        store=access, input_store=inputs, plan_store=plans, demand_store=MemoryDemandStore(plans) if empty else None,
        authenticator=CompanyAuthentication(), clock=clock,
        job_store=jobs, job_controller=controller)


if __name__ == '__main__':
    import uvicorn
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path, default=ROOT / 'docs/acceptance/frozen')
    parser.add_argument('--scenario', required=True)
    parser.add_argument('--port', type=int, default=8776)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='auto-plan-acceptance-adapter-') as directory:
        cert, key = Path(directory) / 'cert.pem', Path(directory) / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key), '-out', str(cert), '-days', '1', '-subj', '/CN=localhost'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        uvicorn.run(application(args.fixtures, args.scenario, args.port), host='127.0.0.1', port=args.port,
                    ssl_keyfile=str(key), ssl_certfile=str(cert), access_log=False)
