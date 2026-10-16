"""Explicit one-job native quantity successor and raw public proof retention."""
from datetime import date, timedelta
import hashlib
import json
import os
import re
from pathlib import Path


def quantity_inputs():
    from auto_plan.input_models import InputSnapshot
    from tests.test_plan_preview import snapshot
    data = InputSnapshot.model_validate(snapshot()['inputs']).model_dump(mode='json')
    for row in data['parameters']['capacities']:
        row['pieces'] = 2000
    data['business']['demands'] = [{'model_code': 'M', 'week': '2026-10-12', 'quantity': 1000}]
    return InputSnapshot.model_validate(data | {'input_version': 1})


def quantity_command(source, submission):
    return {'submission_id': submission, 'base_revision': 0, 'input_version': 1, 'latest_adoption': 0,
        'reviewed_as_of': source['snapshot']['time']['as_of'],
        'request': {'mode': 'local', 'scope': {'start_date': '2026-10-15', 'end_date': '2026-10-15',
            'line_codes': ['L'], 'model_codes': []}, 'strategy': 'cp_sat', 'time_limit_seconds': 20,
            'max_candidates': 1, 'native_threads': 1}}


def quantity_objective(evaluation, window_start):
    start = date.fromisoformat(window_start)
    weeks = [(start + timedelta(days=7*number)).isoformat() for number in range(15)]
    rows = evaluation['weekly_results']
    values = [str(sum(row['demand']-row['on_time'] for row in rows if row['week']==week)) for week in weeks]
    values += [str(sum(row['gap'] for row in rows if row['week']==week)) for week in weeks]
    values += [str(evaluation['switch_count']), str(evaluation['stability']['weighted_cost']), str(evaluation['stability']['order_change_count'])]
    return values


def native_quantity_stage(records, owner):
    rows = [row for row in records if row.get('kind') == 'progress'
        and all(row.get(key) == owner[key] for key in ('job_id', 'run_id', 'generation'))
        and row.get('stage') == 'native_local' and row.get('status') == 'restricted_model'
        and row.get('route') == 'cp_sat' and isinstance(row.get('native_layers'), list)
        and len(row['native_layers']) == 33
        and [layer.get('layer') for layer in row['native_layers']] == list(range(33))]
    if not rows:
        raise ValueError('No complete native quantity result for the actual owned job/run/generation')
    return rows[-1]


class PublicJobProof:
    """Persist bytes before JSON parsing or business assertions; no auth material."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir()
        self.sequence = 0

    def capture(self, label, response, command=None):
        if not re.fullmatch('[a-z][a-z0-9-]*', label):
            raise ValueError('A bounded artifact label is required')
        stem = f'{self.sequence:04d}-{label}'
        self.sequence += 1
        raw = bytes(response.content)
        name = stem + '-response.json'
        with (self.directory / name).open('xb') as stream:
            stream.write(raw)
        record = {'sequence': self.sequence - 1, 'label': label, 'status': response.status_code,
            'body': {'path': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}}
        if command is not None:
            payload = json.dumps(command, separators=(',', ':'), ensure_ascii=False).encode()
            request_name = stem + '-request.json'
            with (self.directory / request_name).open('xb') as stream:
                stream.write(payload)
            record['command'] = {'path': request_name, 'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
        with (self.directory / 'index.ndjson').open('a') as stream:
            stream.write(json.dumps(record, separators=(',', ':')) + '\n')
        return json.loads(raw)


def collect_job_cleanup(observer):
    from scripts.acceptance.job_lifecycle_observer import identity
    try:
        records = [json.loads(line) for line in observer.path.read_text().splitlines()]
        ledger_status = 'complete'
    except FileNotFoundError:
        records, ledger_status = [], 'absent'
    except (OSError, ValueError, UnicodeError):
        records, ledger_status = [], 'unreadable'
    owned = []
    for process, record in observer.processes.values():
        try:
            os.killpg(record['pgid'], 0)
            group_closed = False
        except ProcessLookupError:
            group_closed = True
        except OSError:
            group_closed = None
        owned.append({**record, 'actual_identity': identity(record['pid']), 'returncode': process.child.poll(), 'group_closed': group_closed})
    return records, {'ledger_status':ledger_status, 'job_acquired':bool(owned) or any(row.get('kind') in {'spawned','spawn_unknown'} for row in records), 'owned':owned,
        'all_closed':None if not owned else all(row['actual_identity'] is None and row['returncode'] is not None and row['group_closed'] for row in owned), 'acceptance':'not_run', 'repeats':[]}
