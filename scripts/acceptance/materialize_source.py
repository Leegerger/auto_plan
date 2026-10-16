"""Materialize schema defaults in an independently captured expected source only."""
import argparse
import json
import hashlib
from pathlib import Path

from auto_plan import plan_models
from auto_plan.plan_models import EvaluationSnapshot


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--provenance', type=Path, required=True)
    args = parser.parse_args()
    state = json.loads(args.state.read_text())
    expected = EvaluationSnapshot.model_validate(state['snapshot']).model_dump(mode='json')
    module = Path(plan_models.__file__).resolve()
    args.provenance.write_text(json.dumps({'model_module': str(module), 'model_sha256': hashlib.sha256(module.read_bytes()).hexdigest()}) + '\n')
    print(json.dumps(expected, ensure_ascii=False, separators=(',', ':')))
