"""The concurrent browser adapter pins reads without blocking input commits."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from auto_plan.input_models import InputSnapshot
from scripts.acceptance.adapter_server import BrowserPlanStore
from tests.input_support import MemoryInputStore


def test_input_commit_completes_while_plan_read_keeps_its_original_snapshot():
    inputs = MemoryInputStore()
    inputs.versions[1] = InputSnapshot.model_validate({'input_version': 1,
        'masters': {'lines': [{'code': 'L', 'name': 'Original', 'active': True}]}})
    inputs.version = 1
    plans = BrowserPlanStore(inputs)
    now = datetime(2026, 10, 12, tzinfo=timezone.utc)

    def change(current):
        value = current.model_dump(mode='json')
        value['input_version'] += 1
        value['masters']['lines'][0]['name'] = 'Changed'
        return InputSnapshot.model_validate(value)

    with ThreadPoolExecutor(max_workers=1) as writers:
        def read(unit):
            assert unit.inputs.input_version == 1
            # Completing this future would deadlock if the read held the input
            # writer lock; the timeout is a correctness bound, not an SLA gate.
            saved = writers.submit(inputs.update, 'EDITOR', now, change).result(timeout=2)
            assert saved.input_version == 2
            assert inputs.read().masters.lines[0].name == 'Changed'
            assert unit.inputs.input_version == 1
            assert unit.inputs.masters.lines[0].name == 'Original'
        plans.read('READER', read)
    assert plans.inputs.input_version == 2
