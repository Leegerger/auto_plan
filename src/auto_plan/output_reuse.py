"""Bounded owned values from this process's checked pure output arithmetic."""
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import sys
from threading import RLock
from typing import TypedDict
from pydantic import ConfigDict, TypeAdapter, with_config
from .plan_models import OutputSource, OutputAttribution, WeeklyResult, OutputTotals, ReconciledCell
from .production_time import production_date, production_cutoff


@with_config(ConfigDict(cache_strings=False))
class OutputPart(TypedDict):
    output_sources: list[OutputSource]
    attributions: list[OutputAttribution]
    weekly_results: list[WeeklyResult]


_part = TypeAdapter(OutputPart)
_cells = TypeAdapter(list[ReconciledCell])
_fields = ('output_sources', 'attributions', 'weekly_results')


@dataclass(frozen=True, slots=True)
class _Object:
    items: tuple


def _freeze(value, budget):
    budget[0] -= 1
    if budget[0] < 0:
        raise ValueError('Derived output node limit')
    if value is None or type(value) in (str, int, bool):
        return value
    if type(value) is list:
        return tuple(_freeze(child, budget) for child in value)
    if type(value) is dict:
        return _Object(tuple((_freeze(key, budget), _freeze(child, budget)) for key, child in value.items()))
    raise ValueError('Unsupported derived output value')


def _thaw(value):
    if type(value) is _Object:
        return {key: _thaw(child) for key, child in value.items}
    if type(value) is tuple:
        return [_thaw(child) for child in value]
    return value


def _size(value):
    seen = set()
    def visit(value):
        if id(value) in seen:
            return 0
        seen.add(id(value))
        children = (value.items,) if type(value) is _Object else value if type(value) is tuple else ()
        return sys.getsizeof(value) + sum(visit(child) for child in children)
    return visit(value), len(seen)


def output_json(result):
    """Convert fresh typed output rows with their existing JSON serializers."""
    return result | _part.dump_python({field: result[field] for field in _fields}, mode='json')


class OutputReuse:
    """Only checked, reconciled arithmetic may populate immutable JSON graphs.

    Every caller still reads/authenticates Oracle and validates its complete
    current snapshot. Confirmations, invalid resets and errors use complete fresh
    arithmetic. The default independent evaluator never supplies this object.
    """
    def __init__(self, *, max_entries=256, max_bytes=64*1024*1024, max_entry_bytes=2*1024*1024,
                 max_nodes=1000000, max_entry_nodes=100000):
        self.max_entries, self.max_bytes, self.max_entry_bytes = max_entries, max_bytes, max_entry_bytes
        self.max_nodes, self.max_entry_nodes = max_nodes, max_entry_nodes
        self._entries = OrderedDict()
        self._empty_container_bytes = sys.getsizeof(self._entries)
        self._bytes = self._hits = self._misses = self._evictions = 0
        self._nodes = 0
        self._lock = RLock()

    def _retained_bytes(self):
        # Excludes the fixed empty cache shell/lock. Include variable map
        # storage, even after eviction; clear an empty map to release it.
        return self._bytes + max(0, sys.getsizeof(self._entries) - self._empty_container_bytes)

    def stats(self):
        with self._lock:
            return {'entries': len(self._entries), 'bytes': self._retained_bytes(), 'nodes': self._nodes, 'hits': self._hits,
                    'misses': self._misses, 'evictions': self._evictions}

    def _get(self, key):
        with self._lock:
            value = self._entries.get(key)
            if value is None:
                self._misses += 1
            else:
                self._hits += 1
                self._entries.move_to_end(key)
            return value[0] if value is not None else None

    def _put(self, key, part):
        # Validate every derived field once, including model_copy updates.
        # No input DTO, mutable result or parallel byte payload is retained.
        try:
            # JSON serializers can turn an invalid copied bool quantity into
            # an integer. Validate original Python field types before encoding.
            checked = _part.validate_python(_part.dump_python(part, mode='python'))
            raw = _part.dump_json(checked)
            if len(raw) > min(self.max_bytes, self.max_entry_bytes):
                return None
            checked = _part.validate_json(raw)
            value = _freeze(_part.dump_python(checked, mode='json'), [self.max_entry_nodes])
        except ValueError:
            return None
        payload_bytes, nodes = _size(value)
        weight = payload_bytes + sys.getsizeof(key) + sys.getsizeof(key[1]) + sys.getsizeof((value, 0, 0))
        # Stored counters and a conservative per-entry bookkeeping reserve.
        # The known module producer's reference lives in the counted key tuple;
        # its pre-existing module globals are not recursively retained payload.
        weight += sys.getsizeof(self.max_bytes) + sys.getsizeof(self.max_nodes) + 128
        if (weight > self.max_bytes or payload_bytes > self.max_entry_bytes or self.max_entries <= 0
                or nodes > self.max_entry_nodes or nodes > self.max_nodes):
            return None
        with self._lock:
            old = self._entries.pop(key, None)
            self._bytes += weight - (old[1] if old is not None else 0)
            self._nodes += nodes - (old[2] if old is not None else 0)
            self._entries[key] = value, weight, nodes
            while (len(self._entries) > self.max_entries or self._retained_bytes() > self.max_bytes
                   or self._nodes > self.max_nodes):
                _, removed = self._entries.popitem(last=False)
                self._bytes -= removed[1]
                self._nodes -= removed[2]
                self._evictions += 1
                if not self._entries:
                    self._entries.clear()
        return value

    @staticmethod
    def _keys(snapshot, index, by_model, demands, compute):
        # Reconciliation has already checked raw cutoff/fact validity. Retain
        # explicit temporal classes in addition to the exact resulting cells.
        cutoff = snapshot.execution.cutoff
        common = {'window_start': snapshot.time.window_start.isoformat(),
                  'window_end': snapshot.time.window_end.isoformat(),
                  'production_day': production_date(snapshot.time.as_of).isoformat(),
                  'valid_cutoff': cutoff is not None and cutoff <= snapshot.time.as_of,
                  'cutoff_day': production_date(cutoff).isoformat() if cutoff is not None else None,
                  'holidays': sorted((day.isoformat(), value) for day, value in index.holidays.items())}
        reset = snapshot.reset
        reset_rows = {row.model_code: row for row in reset.models} if reset else {}
        common['reset'] = {'batch_id': reset.batch_id, 'production_week': reset.production_week.isoformat(),
                           'cutoff': reset.cutoff.isoformat()} if reset else None
        keys = {}
        for model, cells in by_model.items():
            months = {row.date.strftime('%Y-%m') for row in cells if row.quantity}
            if reset and model in reset_rows and reset_rows[model].wip:
                months.add(reset.production_week.strftime('%Y-%m'))
            parameters = [(month, index.critical_parameters(month, model)) for month in sorted(months)]
            header = common | {'model': model, 'demands': [r.model_dump(mode='json') for r in demands[model]],
                               'parameters': parameters,
                               'reset_model': reset_rows[model].model_dump(mode='json') if model in reset_rows else None}
            digest = hashlib.sha256()
            for raw in (json.dumps(header, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode(),
                        _cells.dump_json(cells)):
                digest.update(len(raw).to_bytes(8, 'big'))
                digest.update(raw)
            # No stored evaluation or another algorithm can seed these entries.
            keys[model] = compute, digest.digest()
        return keys

    def evaluate(self, snapshot, index, cells, compute, *, json_mode=False):
        def complete():
            result = compute(snapshot, cells, index)
            return output_json(result) if json_mode else result
        reset = snapshot.reset
        if snapshot.inputs.business.confirmations:
            return complete()
        if reset and (reset.production_week != snapshot.time.window_start or
                      reset.cutoff != production_cutoff(reset.production_week) or
                      len({row.model_code for row in reset.models}) != len(reset.models)):
            return complete()
        by_model = defaultdict(list)
        demands = defaultdict(list)
        for cell in cells:
            by_model[cell.model_code].append(cell)
        for row in snapshot.inputs.business.demands:
            if snapshot.time.window_start <= row.week <= snapshot.time.window_end:
                demands[row.model_code].append(row)
                by_model[row.model_code]
        if reset:
            for row in reset.models:
                by_model[row.model_code]
        if not by_model:
            return complete()
        keys = self._keys(snapshot, index, by_model, demands, compute)
        stored = {model: self._get(key) for model, key in keys.items()}
        misses = [model for model, raw in stored.items() if raw is None]
        if len(misses) > max(1, len(keys)//4):
            result = compute(snapshot, cells, index)
            if not result['issues']:
                parts = {model: {field: [] for field in _fields} for model in keys}
                for field in _fields:
                    for row in result[field]:
                        parts[row.model_code][field].append(row)
                for model, part in parts.items():
                    self._put(keys[model], part)
            return output_json(result) if json_mode else result
        parts = {}
        for model, frozen in stored.items():
            if frozen is not None:
                owned = _thaw(frozen)
                parts[model] = owned if json_mode else _part.validate_python(owned)
            else:
                business = snapshot.inputs.business.model_copy(update={'demands': demands[model]})
                selected_reset = reset.model_copy(update={'models': [row for row in reset.models if row.model_code == model]}) if reset else None
                selected = snapshot.model_copy(update={'inputs': snapshot.inputs.model_copy(update={'business': business}),
                                                       'reset': selected_reset})
                result = compute(selected, by_model[model], index)
                if result['issues']:
                    return complete()
                part = {field: result[field] for field in _fields}
                frozen = self._put(keys[model], part)
                if frozen is None:
                    return complete()
                owned = _thaw(frozen)
                parts[model] = owned if json_mode else _part.validate_python(owned)
        from .output_rules import rational, rate_display
        get = dict.__getitem__ if json_mode else getattr
        sources = [row for part in parts.values() for row in part['output_sources']]
        sources.sort(key=lambda row: (min(get(p, 'date') for p in get(row, 'portions')), get(row, 'kind') != 'wip', get(row, 'input_date'), get(row, 'id')))
        source_by_id = {get(row, 'id'): row for row in sources}
        attributions = [row for part in parts.values() for row in part['attributions']]
        attributions.sort(key=lambda row: (get(row, 'date'), get(source_by_id[get(row, 'source_id')], 'kind') != 'wip',
                                           get(source_by_id[get(row, 'source_id')], 'input_date'), get(row, 'source_id')))
        weekly = [row for part in parts.values() for row in part['weekly_results']]
        weekly.sort(key=lambda row: (get(row, 'week'), get(row, 'model_code')))
        demand_total = sum(get(row, 'demand') for row in weekly)
        on_time = sum(get(row, 'on_time') for row in weekly)
        end = snapshot.time.window_end.isoformat() if json_mode else snapshot.time.window_end
        totals = OutputTotals(computation_complete=True, expected=sum(get(p, 'quantity') for s in sources for p in get(s, 'portions')),
            outside_window=sum(get(p, 'quantity') for s in sources for p in get(s, 'portions') if get(p, 'date') > end),
            unallocated=sum(get(s, 'unallocated_output') for s in sources), on_time=on_time,
            late=sum(get(row, 'late') for row in weekly), pending=sum(get(row, 'pending') for row in weekly), gap=sum(get(row, 'gap') for row in weekly),
            on_time_rate=rational(Fraction(on_time, demand_total)) if demand_total else None,
            on_time_rate_display=rate_display(on_time, demand_total))
        return {'issues': [], 'reconciled_cells': cells, 'output_sources': sources, 'attributions': attributions,
                'weekly_results': weekly, 'confirmation_usage': [], 'output_totals': totals,
                'excluded_demands': [r for r in snapshot.inputs.business.demands
                                     if not snapshot.time.window_start <= r.week <= snapshot.time.window_end]}
