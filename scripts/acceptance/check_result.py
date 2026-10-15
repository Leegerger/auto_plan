"""Compare an observed public response with a frozen reference without numeric string ordering."""
import argparse
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.acceptance.compare import assert_reference, assert_candidate, numeric, objective
from scripts.acceptance.validate import load

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path, required=True)
    parser.add_argument('--scenario', required=True)
    parser.add_argument('--evaluation', type=Path, required=True)
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--observed-source', type=Path)
    parser.add_argument('--kind', choices=['reference', 'candidate', 'source'], required=True)
    args = parser.parse_args()
    scenario = next(row for row in json.loads((args.fixtures / 'manifest.json').read_text())['scenarios'] if row['id'] == args.scenario)
    evaluation = json.loads(args.evaluation.read_text())
    if args.kind == 'reference':
        vector = assert_reference(evaluation, scenario)
    else:
        source = load(args.fixtures, scenario['source'])
        plan = json.loads(args.candidate.read_text())
        if args.kind == 'source':
            if args.observed_source:
                from auto_plan.plan_models import EvaluationSnapshot
                frozen = EvaluationSnapshot.model_validate(source).model_dump(mode='json')
                observed = EvaluationSnapshot.model_validate(json.loads(args.observed_source.read_text())).model_dump(mode='json')
                observed['inputs']['input_version'] = frozen['inputs']['input_version']
                if observed['baseline']: observed['baseline']['version'] = frozen['baseline']['version']
                # An explicit external snapshot containing only zero WIP/balance is equivalent to absent reset output.
                for value in (frozen, observed):
                    if value['reset'] and all(not row['wip'] and not row['net_balance'] for row in value['reset']['models']): value['reset'] = None
                assert numeric(observed) == numeric(frozen), 'Prepared input/context/baseline differs from frozen source.'
            for key in ('segments', 'switches', 'initial_states', 'ordinary_idle', 'current_day_witnesses'):
                observed, expected = plan.get(key, []), source['plan'].get(key, [])
                if key in ('segments', 'switches'): observed, expected = sorted(observed, key=lambda row: row['id']), sorted(expected, key=lambda row: row['id'])
                assert numeric(observed) == numeric(expected), 'Prepared source plan differs from frozen source.'
            vector = objective(evaluation)
        else:
            vector = assert_candidate(evaluation, scenario, source, plan)
    print(json.dumps({'status': 'pass', 'objective': vector}))
