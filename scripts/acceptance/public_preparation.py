"""Public source readback and original-UUID journaling for fixture preparation."""
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4

from scripts.acceptance.local_v2 import OutcomeUnknown


def validate_target(config):
    target = config['target']
    assert target['disposable'] is True and target['production'] is False, 'Disposable test target required.'
    assert target['kind'] in ('adapter', 'PLAN_TEST'), 'Unsupported test target.'
    if target['kind'] == 'PLAN_TEST':
        assert re.fullmatch(r'AP_TEST_[A-F0-9]{6}', target['namespace']), 'Invalid disposable namespace.'
    return target


def fingerprint(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    return hashlib.sha256(raw).hexdigest()


class PublicPreparation:
    """Own an append-only operation journal, public commits and pinned readback."""
    def __init__(self, call, config, output, **provenance):
        self.call, self.output = call, Path(output)
        target = validate_target(config)
        self.output.mkdir(parents=True, exist_ok=False)
        self.report = {'kind': 'public API preparation; not timed acceptance', 'scenario': config['scenario'],
                       'target': target, 'public_api_only': True, 'status': 'in_progress', 'operations': [], **provenance}
        self.persist()

    def retain(self, name, value):
        assert Path(name).name == name, 'Artifact name must be local.'
        (self.output / name).write_text(json.dumps(value, indent=2) + '\n')

    def persist(self):
        self.retain('report.json', self.report)

    def phase(self, name):
        self.report['phase'] = name
        self.persist()

    def state(self):
        head = self.call('/plans/current')
        inputs = self.call('/inputs?version=' + str(head['input_version']))
        baseline = None
        if head['latest_adoption']:
            adopted = self.call('/plans/adoptions/' + str(head['latest_adoption']))
            baseline = {'version': head['latest_adoption'], 'plan': adopted['snapshot']['plan'], 'coverage': adopted['coverage']}
        context = head['context']
        return {'head': head, 'snapshot': {'time': head['time'], 'inputs': inputs, 'plan': head['plan'],
                **{key: context[key] for key in ('execution', 'reset', 'locks', 'preferences')}, 'baseline': baseline}}

    def commit(self, path, payload, kind):
        key = str(uuid4())
        command = payload | {'operation_id': key}
        operation = {'kind': kind, 'operation_id': key, 'request': command, 'request_sha256': fingerprint(command),
                     'status': 'prepared', 'unknown_outcome_reconciled': False}
        self.report['operations'].append(operation)
        self.persist()
        try:
            result = self.call(path, command)
        except OutcomeUnknown:
            operation['status'] = 'unknown'
            self.persist()
            result = self.call('/plans/verify', {'operation_id': key})
            operation['unknown_outcome_reconciled'] = True
        operation['result'] = result
        self.persist()
        assert result['status'] == 'COMMITTED', 'Original UUID is unresolved; preserve the journal before continuing.'
        verification = self.call('/plans/verify', {'operation_id': key})
        operation['verification'] = verification
        assert verification == result, 'Original UUID verification differs from the commit result.'
        operation['status'] = 'COMMITTED'
        self.persist()
        return result

    def inputs(self, rows, kind):
        head = self.call('/plans/current')
        command = {'base_version': head['input_version'], 'changes': {'confirmations': rows}}
        preview = self.call('/inputs/preview', command)
        assert preview['valid'], 'Temporary or restored confirmation is invalid.'
        operation = {'kind': kind, 'request': command, 'request_sha256': fingerprint(command),
                     'status': 'prepared', 'protocol': 'maintained input optimistic version; no Plan UUID'}
        self.report['operations'].append(operation)
        self.persist()
        try:
            result = self.call('/inputs/apply', command)
        except OutcomeUnknown:
            operation['status'] = 'unknown'
            self.persist()
            raise
        operation['result'] = result
        expected = preview['snapshot'] | {'input_version': head['input_version'] + 1}
        assert result == expected, 'Input commit differs from the complete preview.'
        assert self.call('/inputs?version=' + str(result['input_version'])) == result, 'Immutable input readback differs.'
        operation['status'] = 'COMMITTED'
        self.persist()
        return result

    def failed(self, error):
        self.report.update(status='fail', failure_type=type(error).__name__)
        self.persist()
        self.retain('failure.json', {'status': 'fail', 'phase': self.report.get('phase'), 'reason': type(error).__name__})
