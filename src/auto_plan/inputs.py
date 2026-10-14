"""Atomic input previews and mutations shared by JSON, Excel and pasted tables."""
from copy import deepcopy
from datetime import datetime
from typing import Any, Callable, Protocol
from pydantic import ValidationError
from .input_models import InputSnapshot, Master, Model, Capability, LineYield, ModelYield, Capacity, CycleTime, SwitchDuration, Holiday, OpeningException, Occupancy, normalize_snapshot

ROW_TYPES = {'recipes': Master, 'lines': Master, 'models': Model, 'capabilities': Capability}
SECTIONS = {kind: 'masters' for kind in ROW_TYPES}
KEYS = {'recipes': ('code',), 'lines': ('code',), 'models': ('code',), 'capabilities': ('recipe_code', 'line_code')}
ROW_TYPES.update(line_yields=LineYield, model_yields=ModelYield, capacities=Capacity, cycle_times=CycleTime, switch_durations=SwitchDuration)
SECTIONS.update({kind: 'parameters' for kind in ['line_yields', 'model_yields', 'capacities', 'cycle_times', 'switch_durations']})
KEYS.update(line_yields=('month',), model_yields=('month', 'model_code'), capacities=('month', 'recipe_code', 'line_code'), cycle_times=('model_code',), switch_durations=('from_recipe', 'to_recipe'))
ROW_TYPES.update(holidays=Holiday, opening_exceptions=OpeningException, maintenance=Occupancy, experiments=Occupancy)
SECTIONS.update({kind: 'calendar' for kind in ['holidays', 'opening_exceptions', 'maintenance', 'experiments']})
KEYS.update(holidays=('date',), opening_exceptions=('date', 'line_code'), maintenance=('date', 'line_code'), experiments=('date', 'line_code'))


class InputConflict(Exception):
    pass


class InputErrors(Exception):
    def __init__(self, errors):
        self.errors = errors


class InputStore(Protocol):
    def read(self, version: int | None = None) -> InputSnapshot: ...
    def update(self, actor: str, now: datetime, transform: Callable[[InputSnapshot], InputSnapshot]) -> InputSnapshot: ...


def error(sheet, row, field, reason):
    return {'sheet': sheet, 'row': row, 'field': field, 'reason': reason}


def preview(current: InputSnapshot, base_version: int, changes: dict[str, list[dict[str, Any]]]):
    if base_version != current.input_version:
        raise InputConflict
    original = current.model_dump(mode='json')
    candidate = deepcopy(original)
    locations = {kind: {tuple(row[field] for field in KEYS[kind]): index
                       for index, row in enumerate(candidate[SECTIONS[kind]][kind])} for kind in ROW_TYPES}
    original_rows = {kind: {tuple(row[field] for field in KEYS[kind]): row
                           for row in original[SECTIONS[kind]][kind]} for kind in ROW_TYPES}
    errors, normalized = [], {}
    for kind, rows in changes.items():
        if kind not in ROW_TYPES:
            errors.append(error(kind, 1, 'sheet', '未知资料类别'))
            continue
        normalized[kind] = []
        seen = set()
        for index, raw in enumerate(rows, 2):
            source_row = raw.get('_row', index)
            if type(source_row) is not int or source_row < 2:
                source_row = index
            values = {key: value for key, value in raw.items() if key != '_row'}
            try:
                item = ROW_TYPES[kind].model_validate(values).model_dump(mode='json')
            except ValidationError as exc:
                for detail in exc.errors():
                    reason = detail['ctx']['error'] if 'ctx' in detail and 'error' in detail['ctx'] else '字段必填或格式无效'
                    errors.append(error(kind, source_row, str(detail['loc'][0]), str(reason)))
                continue
            key = tuple(item[field] for field in KEYS[kind])
            if key in seen:
                errors.append(error(kind, source_row, ','.join(KEYS[kind]), '批内识别组合重复'))
            seen.add(key)
            normalized[kind].append((source_row, item))
            target = candidate[SECTIONS[kind]][kind]
            position = locations[kind].get(key)
            previous = target[position] if position is not None else None
            if kind == 'models' and previous and item['recipe_code'] != previous['recipe_code']:
                errors.append(error(kind, source_row, 'recipe_code', '机种的 Recipe 固定，不可更换'))
            if previous:
                target[position] = item
            else:
                locations[kind][key] = len(target)
                target.append(item)
    # Master sheets can arrive in any order within the same batch.
    master_index = {kind: {row['code']: row for row in candidate['masters'][kind]}
                    for kind in ['recipes', 'lines', 'models']}
    for kind, rows in normalized.items():
        for source_row, item in rows:
            for field, master_kind in [('recipe_code', 'recipes'), ('line_code', 'lines'), ('model_code', 'models'), ('from_recipe', 'recipes'), ('to_recipe', 'recipes')]:
                if field not in item:
                    continue
                master = master_index[master_kind].get(item[field])
                old = original_rows[kind].get(tuple(item[k] for k in KEYS[kind]))
                if master is None:
                    errors.append(error(kind, source_row, field, '编码不存在，请先维护主资料'))
                elif not master['active'] and old is None:
                    errors.append(error(kind, source_row, field, '已停用的编码不能新增引用'))
    return {'valid': not errors, 'base_version': current.input_version, 'errors': errors,
            'changes': {kind: [item for _, item in rows] for kind, rows in normalized.items()},
            'snapshot': candidate if not errors else None}


def apply_batch(current, base_version, changes):
    result = preview(current, base_version, changes)
    if result['errors']:
        raise InputErrors(result['errors'])
    if result['snapshot'] == current.model_dump(mode='json'):
        return current
    result['snapshot']['input_version'] += 1
    return normalize_snapshot(result['snapshot'])


def continue_months(current: InputSnapshot, month: str) -> InputSnapshot:
    document = current.model_dump(mode='json')
    start_year, start_month = map(int, month.split('-'))
    start = start_year * 12 + start_month - 1
    for kind in ('line_yields', 'model_yields', 'capacities'):
        rows = document['parameters'][kind]
        for index in range(start, start + 5):
            current_month = f'{index // 12:04d}-{index % 12 + 1:02d}'
            prior_month = f'{(index - 1) // 12:04d}-{(index - 1) % 12 + 1:02d}'
            keys = KEYS[kind][1:]
            present = {tuple(r[k] for k in keys) for r in rows if r['month'] == current_month}
            for old in list(rows):
                key = tuple(old[k] for k in keys)
                if old['month'] == prior_month and key not in present:
                    rows.append(deepcopy(old) | {'month': current_month})
                    present.add(key)
    if document == current.model_dump(mode='json'):
        return current
    document['input_version'] += 1
    return normalize_snapshot(document)


def selection_options(current: InputSnapshot):
    recipes = {r.code for r in current.masters.recipes if r.active}
    return {'recipes': [r for r in current.masters.recipes if r.active],
            'lines': [r for r in current.masters.lines if r.active],
            'models': [m for m in current.masters.models if m.active and m.recipe_code in recipes]}


def availability(current: InputSnapshot, month: str, model_code: str, line_code: str, production_date: str | None, from_recipe: str | None):
    from .input_lookup import InputIndex
    from .input_models import date_value
    return InputIndex(current).availability(month, model_code, line_code,
        date_value(production_date) if production_date else None, from_recipe)
