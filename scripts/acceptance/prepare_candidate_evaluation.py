"""Prepare a transient public evaluation request without rewriting its historical source."""
import argparse
import hashlib
import json
from pathlib import Path

from auto_plan import input_models, inputs
from auto_plan.input_models import CurrentInputSnapshot, InputSnapshot


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--provenance', type=Path, required=True)
    args = parser.parse_args()
    snapshot = json.loads(args.snapshot.read_text())
    historical = InputSnapshot.model_validate(snapshot['inputs'])
    rows = snapshot['inputs']['parameters']['line_yields']
    legacy = [row for row in rows if row.get('model_code') is None]
    if legacy and len(legacy) != len(rows):
        raise ValueError('Mixed legacy and model-specific yields cannot be reinterpreted.')
    if legacy:
        # Copy only the historical uniform values to the models in that same
        # snapshot. Do not read current inputs, advance versions or fill months.
        expanded = inputs.expanded_legacy_line_yields(historical)
        snapshot['inputs']['parameters']['line_yields'] = expanded.model_dump(mode='json')['parameters']['line_yields']
    CurrentInputSnapshot.model_validate(snapshot['inputs'])
    modules = [Path(module.__file__).resolve() for module in (inputs, input_models)]
    args.provenance.write_text(json.dumps({
        'kind': 'historical_uniform_yields_expanded' if legacy else 'unchanged_current_format',
        'input_version': snapshot['inputs']['input_version'],
        'modules': {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in modules},
    }) + '\n')
    print(json.dumps({'snapshot': snapshot}, ensure_ascii=False, separators=(',', ':')))
