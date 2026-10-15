"""Compare an observed public response with a frozen reference without numeric string ordering."""
import argparse
import json
import hashlib
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.acceptance.compare import assert_reference, assert_candidate, numeric, objective
from scripts.acceptance.validate import load
from scripts.acceptance.assertions import assert_frozen_snapshot, assert_provenance, assert_workbook
from scripts.acceptance.input_companion import approved_source

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path, required=True)
    parser.add_argument('--scenario', required=True)
    parser.add_argument('--evaluation', type=Path, required=True)
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--observed-source', type=Path)
    parser.add_argument('--proof', type=Path)
    parser.add_argument('--kind', choices=['reference', 'candidate', 'source', 'workbook'], required=True)
    args = parser.parse_args()
    manifest = json.loads((args.fixtures / 'manifest.json').read_text())
    scenario = next(row for row in manifest['scenarios'] if row['id'] == args.scenario)
    evaluation = json.loads(args.evaluation.read_text())
    if args.kind == 'workbook':
        proof = json.loads(args.proof.read_text())
        assert_workbook(proof['prepared'], load(args.fixtures, scenario['import']), load(args.fixtures, scenario['source']), scenario, proof['before'], proof['after'])
        vector = objective(proof['prepared']['evaluation'])
    elif args.kind == 'reference':
        vector = assert_reference(evaluation, scenario)
    else:
        source = load(args.fixtures, scenario['source'])
        plan = json.loads(args.candidate.read_text())
        if args.kind == 'source':
            if args.observed_source:
                observed = json.loads(args.observed_source.read_text())
                source = approved_source(args.fixtures, manifest, scenario, source, observed['inputs'])
                assert_frozen_snapshot(observed, source)
            for key in ('segments', 'switches', 'initial_states', 'ordinary_idle', 'current_day_witnesses'):
                observed, expected = plan.get(key, []), source['plan'].get(key, [])
                if key in ('segments', 'switches'): observed, expected = sorted(observed, key=lambda row: row['id']), sorted(expected, key=lambda row: row['id'])
                assert numeric(observed) == numeric(expected), 'Prepared source plan differs from frozen source.'
            vector = objective(evaluation)
        else:
            vector = assert_candidate(evaluation, scenario, source, plan)
            assert args.proof is not None, 'Candidate frozen provenance is required.'
            proof = json.loads(args.proof.read_text())
            accepted_inputs = None
            if proof['source'].get('kind')=='public_computation_source':
                state=proof['source']
                assert state['status']==200,'Computation source needs a successful complete public read.'
                raw=(args.proof.parent/state['artifact']['path']).read_bytes()
                assert hashlib.sha256(raw).hexdigest()==state['artifact']['sha256'],'Public computation source body bytes differ.'
                body=json.loads(raw)
                assert body['snapshot']==state['snapshot'],'Public computation source snapshot differs from captured bytes.'
                assert state['head']=={'revision':body['source_revision'],'input_version':body['input_version'],'latest_adoption':body['latest_adoption']},'Computation source tokens differ from captured bytes.'
                assert proof['submitted']['reviewed_as_of']==body['snapshot']['time']['as_of'],'Submitted review time differs from the complete source read.'
            if proof['source'].get('accepted_inputs'):
                capture = proof['source']['accepted_inputs']
                assert capture['status'] == 200, 'Accepted source needs a successful immutable public input read.'
                raw = (args.proof.parent / capture['artifact']['path']).read_bytes()
                assert hashlib.sha256(raw).hexdigest() == capture['artifact']['sha256'], 'Immutable public input body bytes differ.'
                accepted_inputs = json.loads(raw)
                assert accepted_inputs['input_version'] == capture['version'], 'Immutable public input read returned another version.'
                captured = proof['source']['captured_snapshot']
                expected_current = approved_source(args.fixtures, manifest, scenario, source, captured['inputs'])
                assert_frozen_snapshot(captured, expected_current)
            source = approved_source(args.fixtures, manifest, scenario, source, proof['candidate']['snapshot']['inputs'])
            assert_provenance(proof, source, load(args.fixtures, scenario['action']), evaluation, accepted_inputs)
    print(json.dumps({'status': 'pass', 'objective': vector}))
