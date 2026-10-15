"""Selected-version workbook. No business calculations or current input reads."""
import hashlib
import json
import re
from io import BytesIO
from openpyxl import Workbook
from .exact import hours

EXACT_FIELDS = {'hours', 'idle_hours', 'prior_switch_hours', 'remaining_hours', 'fraction',
                'before_rounding', 'input_share_exact', 'cycle_time', 'line_yield', 'model_yield',
                'on_time_rate', 'rate', 'weighted_cost', 'tolerance_percent', 'near_week_weight',
                'far_week_weight', 'near_tolerance_percent', 'far_tolerance_percent'}
GROUP_FIELDS = ('source_id', 'model_code', 'demand_week', 'source_week_output',
                'apportionment_denominator', 'apportioned_input', 'input_share_exact',
                'material_input_used', 'material_input_pending')


def excel_text(value):
    if not isinstance(value, str):
        return value
    value = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]',
                   lambda match: f'\\u{ord(match[0]):04x}', value)
    encoded = value.encode('utf-16-le')
    if len(encoded) > 60000:
        value = encoded[:58000].decode('utf-16-le', errors='ignore') + '\n[显示已截短；完整值见 _record_json]'
    return value


def flat_values(row, prefix=''):
    result = {}
    for key, value in row.items():
        name = prefix + key
        if isinstance(value, dict):
            result.update(flat_values(value, name + '_'))
        elif isinstance(value, list):
            continue
        elif type(value) is int:
            result[name + '_display'] = value
            result[name + '_exact'] = str(value)
        elif isinstance(value, str) and key in EXACT_FIELDS:
            result[name + '_exact' if not name.endswith('_exact') else name] = value
            result[name.removesuffix('_exact') + '_display'] = hours(value).display
        else:
            result[name] = value
    return result


def workbook_bytes(record):
    book = Workbook()
    book.remove(book.active)

    def table(name, rows, *, flatten=True):
        rows = [flat_values(row) if flatten else row for row in rows]
        headers = list(dict.fromkeys(key for row in rows for key in row)) or ['state']
        sheet = book.create_sheet(name)
        sheet.append(headers)
        for row in rows:
            sheet.append([excel_text(row.get(key)) for key in headers])
        # Text is literal even when a business code begins with '='. Exact
        # counts remain text so Excel's 15-digit numeric limit cannot lose data.
        for cells in sheet:
            for cell in cells:
                if isinstance(cell.value, str):
                    cell.data_type = 's'
        sheet.freeze_panes = 'A2'
        sheet.auto_filter.ref = sheet.dimensions
        return sheet

    snapshot = record.get('snapshot', {})
    plan, evaluation = snapshot.get('plan', {}), record.get('evaluation', {})
    table('_version', [{key: record.get(key) for key in ('revision', 'latest_adoption', 'input_version', 'actor', 'recorded_at', 'kind', 'pending')}])
    table('production', plan.get('segments', []))
    table('switches', plan.get('switches', []))
    table('switch_allocations', [dict(switch_id=row['id'], line_code=row['line_code'], **allocation) for row in plan.get('switches', []) for allocation in row['allocations']])
    table('ordinary_idle', plan.get('ordinary_idle', []))
    table('initial_states', plan.get('initial_states', []))
    proof = plan.get('current_day_witnesses', [])
    table('current_day_proof', proof)
    table('residual_production', [dict(proof_date=p['date'], proof_line=p['line_code'], **row) for p in proof for row in p.get('segments', [])])
    table('prior_recipe_hours', [dict(date=p['date'], line_code=p['line_code'], **row) for p in proof for row in p.get('prior_recipe_hours', [])])
    for field, name in [('switches', 'residual_switches'), ('prior_switches', 'prior_switches')]:
        table(name, [dict(proof_date=p['date'], proof_line=p['line_code'], switch_id=s['id'], from_recipe=s['from_recipe'], to_recipe=s['to_recipe'], **a)
                     for p in proof for s in p.get(field, []) for a in s['allocations']])
    table('days', evaluation.get('days', []))
    table('recipe_hours', [dict(date=d['date'], line_code=d['line_code'], **r) for d in evaluation.get('days', []) for r in d.get('recipes', [])])
    table('switch_results', evaluation.get('switches', []))
    sources = evaluation.get('output_sources', [])
    table('output_sources', sources)
    table('source_lines', [dict(source_id=s['id'], **r) for s in sources for r in s.get('lines', [])])
    table('output_portions', [dict(source_id=s['id'], portion=n, **r) for s in sources for n, r in enumerate(s.get('portions', []))])
    table('weekly_results', evaluation.get('weekly_results', []))
    table('output_totals', [evaluation['output_totals']] if evaluation.get('output_totals') else [])
    attributions = evaluation.get('attributions', [])
    table('attributions', [dict(portion=n, **row) for n, row in enumerate(attributions)])
    groups = {}
    for row in attributions:
        groups.setdefault((row['source_id'], row['demand_week']), row)
    table('attribution_groups', [{key: row.get(key) for key in GROUP_FIELDS} for row in groups.values()])
    table('material_batches', [dict(source_id=r['source_id'], demand_week=r['demand_week'], **batch) for r in groups.values() for batch in r.get('material_batches', [])])
    table('shipping_batches', [dict(portion=n, source_id=r['source_id'], demand_week=r['demand_week'], date=r['date'], **batch) for n, r in enumerate(attributions) for batch in r.get('shipping_batches', [])])
    table('confirmation_usage', evaluation.get('confirmation_usage', []))
    table('reconciled_cells', evaluation.get('reconciled_cells', []))
    table('issues', evaluation.get('issues', []))
    table('excluded_demands', evaluation.get('excluded_demands', []))
    inputs = snapshot.get('inputs', {})
    for section in ('masters', 'parameters', 'business'):
        for name, rows in inputs.get(section, {}).items():
            table(name, rows)
    table('calendar', [dict(category=key, **row) for key, rows in inputs.get('calendar', {}).items() for row in rows])
    table('execution', [snapshot.get('execution', {})])
    for key in ('facts', 'coverage', 'associations', 'association_cells', 'day_capacities', 'audit'):
        table('execution_' + key, snapshot.get('execution', {}).get(key, []))
    table('execution_changes', [dict(action_id=a['action_id'], **change) for a in snapshot.get('execution', {}).get('audit', []) for change in a.get('changes', [])])
    table('reset', [snapshot['reset']] if snapshot.get('reset') else [])
    table('reset_models', snapshot.get('reset', {}).get('models', []) if snapshot.get('reset') else [])
    table('locks', snapshot.get('locks', []))
    table('baseline_coverage', record.get('coverage', []))
    table('reset_targets', record.get('lineage', {}).get('reset_preview', {}).get('targets', []) if record.get('lineage', {}).get('reset_preview') else [])
    table('preferences', [snapshot.get('preferences', {})])
    stability = evaluation.get('stability')
    table('stability', [stability] if stability else [{'state': 'unrecorded', 'description': '此历史快照未记录稳定性指标'}])
    table('stability_weeks', stability.get('weeks', []) if stability else [])
    table('unallocated', record.get('unallocated', []))
    table('operation_issues', record.get('operation_issues', []))
    # The canonical record is an independent lossless reconstruction path for
    # every nested context, lineage, proof, operation and future additive field.
    # Chunk below Excel's 32767-character cell limit, including large ledgers.
    raw = json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
    table('_manifest', [{'schema': 'auto-plan-history-1', 'sha256': hashlib.sha256(raw.encode()).hexdigest(), 'encoding': 'UTF-8 JSON, ordered chunks',
                         'group_rule': 'attributions: portion rows; attribution_groups: source/week once; confirmation_usage: batch ledger',
                         'text_rule': 'XML-prohibited characters are displayed as \\uXXXX. Long display text is explicitly marked. _record_json retains every original character.',
                         'proof_rule': 'prior_recipe_hours and prior_switches are declared feasibility proof variables, not measured consumption'}], flatten=False)
    table('_record_json', [{'chunk': n // 15000, 'json_chunk': raw[n:n+15000]} for n in range(0, len(raw), 15000)], flatten=False)
    stream = BytesIO()
    book.save(stream)
    return stream.getvalue()
