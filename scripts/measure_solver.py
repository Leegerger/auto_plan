"""CPU-only solver diagnostic, separate from deployed/browser/Oracle SLA gates."""
import argparse
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from auto_plan.solver import solve
from auto_plan.solver_models import FrozenSolveInput
from tests.scale_support import full_window


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--empty', action='store_true')
    parser.add_argument('--mode', choices=('local', 'all_replan', 'new_week'), default='all_replan')
    parser.add_argument('--seconds', type=int, default=20)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    payload = full_window()
    # Future-start diagnostic avoids any unconfirmed current-day capacity.
    # The explicitly frozen window is unchanged; no deployed-time claim follows.
    payload['time']['as_of'] = '2026-10-11T08:00:00+08:00'
    payload['execution'] = {}
    if args.empty:
        payload['plan'] = {}
    elif args.mode == 'new_week':
        payload['plan']['segments'] = [row for row in payload['plan']['segments'] if row['date'] < '2027-01-18']
        payload['plan']['switches'] = [row for row in payload['plan']['switches'] if all(a['date'] < '2027-01-18' for a in row['allocations'])]
    start = '2027-01-24' if args.mode == 'local' else '2027-01-18' if args.mode == 'new_week' else '2026-10-12'
    frozen = FrozenSolveInput(snapshot=payload, source_revision=1, input_version=1, latest_adoption=0,
        request={'mode': args.mode, 'time_limit_seconds': args.seconds,
            'scope': {'start_date': start, 'end_date': '2027-01-24',
                      'line_codes': [f'L{n:02d}' for n in range(18)]}})
    started, events = time.monotonic(), []
    def progress(event):
        record = {'kind': event['kind'], 'seconds': time.monotonic()-started}
        if event['kind'] == 'stage':
            record.update(stage=event['stage'], status=event['status'])
        else:
            record['objective'] = event['candidate']['objective']
        events.append(record)
    result = solve(frozen, progress=progress)
    document = {'diagnostic': 'CPU only, synthetic future start; no container/browser/Oracle SLA acceptance',
        'dimensions': {'lines': 18, 'models': 40, 'recipes': 20, 'days': 105}, 'empty': args.empty,
        'mode': args.mode,
        'status': result.status, 'seconds': result.elapsed_seconds,
        'objectives': [c.objective for c in result.candidates],
        'executable': [c.evaluation.executable for c in result.candidates], 'events': events}
    Path(args.output).write_text(json.dumps(document, indent=2) + '\n')
    print(json.dumps({key: document[key] for key in ('empty', 'status', 'seconds', 'objectives', 'executable')}))


if __name__ == '__main__':
    main()
