"""Populate a downloaded public standard template with a frozen source import."""
import argparse
import hashlib
import json
from datetime import date
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.acceptance.validate import load


def workbook(fixtures, scenario_id, template, output):
    from openpyxl import load_workbook
    if output.exists() or output.with_name(output.name + '.acceptance.json').exists():
        raise FileExistsError('Never overwrite a prepared workbook.')
    scenario = next(row for row in json.loads((fixtures / 'manifest.json').read_text())['scenarios'] if row['id'] == scenario_id)
    payload = load(fixtures, scenario['import'])
    book = load_workbook(template)
    tables = payload['changes'] | {key: payload[key] for key in ('segments', 'demands', 'coverage', 'initial_states')}
    tables['reset'] = [{**payload['reset'], **row} for row in payload['reset']['models']]
    tables['execution_facts'] = payload['execution']['facts']
    tables['execution_coverage'] = payload['execution']['coverage']
    tables['settings'] = [{'complete_weekly_demands': True, 'execution_cutoff': payload['execution']['cutoff']}]
    tables['demands'] = [row | {'week_label': 'W%04d%02d' % date.fromisoformat(row['week']).isocalendar()[:2]} for row in tables['demands']]
    for name, sheet in ((name, book[name]) for name in tables):
        headers = [cell.value for cell in sheet[1]]
        for row in tables[name]:
            sheet.append(['|'.join(row[key]) if isinstance(row.get(key), list) else row.get(key) for key in headers])
    book.save(output)
    provenance = {'schema_version': 1, 'scenario': scenario_id, 'import_sha256': scenario['import']['sha256'], 'builder_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  'manifest_sha256': hashlib.sha256((fixtures / 'manifest.json').read_bytes()).hexdigest(),
                  'workbook_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
                  'row_counts': {name: len(rows) for name, rows in tables.items()}}
    output.with_name(output.name + '.acceptance.json').write_text(json.dumps(provenance, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path, default=ROOT / 'docs/acceptance/frozen')
    parser.add_argument('--scenario', required=True)
    parser.add_argument('--template', type=Path, required=True, help='Download /api/initialization/template.xlsx from the selected test app.')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    workbook(args.fixtures, args.scenario, args.template, args.output)
