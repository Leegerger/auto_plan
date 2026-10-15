"""Opt-in CPU budget regression; deployed/browser SLA belongs to acceptance."""
import os
from time import perf_counter
import pytest
from fastapi.testclient import TestClient
from tests.scale_support import full_window, seeded_app, final_day_command, check_final_day


@pytest.mark.skipif(os.environ.get('AUTO_PLAN_SCALE_TEST') != '1', reason='fixed-environment CPU performance test')
def test_batch_preview_finishes_full_window_with_exact_final_week_gaps_within_five_seconds():
    payload = full_window()
    with TestClient(seeded_app(payload), base_url='https://testserver') as client:
        client.headers.update({'Origin': 'https://testserver', 'X-Auto-Plan-Request': '1'})
        assert client.post('/auto_plan/api/auth/login', json={'username': 'SCALEEDITOR', 'password': 'company-secret'}).status_code == 200
        command = final_day_command(payload)
        for _ in range(3):
            began = perf_counter()
            response = client.post('/auto_plan/api/plans/preview', json=command)
            assert response.status_code == 200, response.text[:1000]
            assert response.headers['content-type'] == 'application/json'
            elapsed = perf_counter() - began
            check_final_day(response.json()['evaluation'])
            assert elapsed <= 5, f'CPU-only batch preview took {elapsed:.3f}s; deployed limit is also 5s'
