"""Bounded owned values from this process's checked pure output arithmetic."""
from collections import OrderedDict, defaultdict
from fractions import Fraction
import hashlib
import json
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


class OutputReuse:
    """Only issue-free, reconciled model arithmetic may populate these bytes.

    Every caller still reads/authenticates Oracle and validates its complete
    current snapshot. Confirmations, invalid resets and errors use complete fresh
    arithmetic. The default independent evaluator never supplies this object.
    """
    def __init__(self, *, max_entries=256, max_bytes=64*1024*1024, max_entry_bytes=2*1024*1024):
        self.max_entries, self.max_bytes, self.max_entry_bytes = max_entries, max_bytes, max_entry_bytes
        self._entries = OrderedDict()
        self._bytes = self._hits = self._misses = self._evictions = 0
        self._lock = RLock()

    def stats(self):
        with self._lock:
            return {'entries': len(self._entries), 'bytes': self._bytes, 'hits': self._hits,
                    'misses': self._misses, 'evictions': self._evictions}

    def _get(self, key):
        with self._lock:
            value = self._entries.get(key)
            if value is None:
                self._misses += 1
            else:
                self._hits += 1
                self._entries.move_to_end(key)
            return value

    def _put(self, key, part):
        raw = _part.dump_json(part)
        weight = len(raw) + len(key[1])
        if weight > self.max_bytes or len(raw) > self.max_entry_bytes or self.max_entries <= 0:
            return
        with self._lock:
            old = self._entries.pop(key, None)
            # Payload and digest bytes count toward the byte cap. Python
            # containers/function references are separately bounded by entries.
            self._bytes += weight - (len(old) + len(key[1]) if old is not None else 0)
            self._entries[key] = raw
            while len(self._entries) > self.max_entries or self._bytes > self.max_bytes:
                removed_key, removed = self._entries.popitem(last=False)
                self._bytes -= len(removed) + len(removed_key[1])
                self._evictions += 1

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

    def evaluate(self, snapshot, index, cells, compute):
        reset = snapshot.reset
        if snapshot.inputs.business.confirmations:
            return compute(snapshot, cells, index)
        if reset and (reset.production_week != snapshot.time.window_start or
                      reset.cutoff != production_cutoff(reset.production_week) or
                      len({row.model_code for row in reset.models}) != len(reset.models)):
            return compute(snapshot, cells, index)
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
            return compute(snapshot, cells, index)
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
            return result
        parts = {}
        for model, raw in stored.items():
            if raw is not None:
                try:
                    parts[model] = _part.validate_json(raw)
                except ValueError:
                    return compute(snapshot, cells, index)
            else:
                business = snapshot.inputs.business.model_copy(update={'demands': demands[model]})
                selected_reset = reset.model_copy(update={'models': [row for row in reset.models if row.model_code == model]}) if reset else None
                selected = snapshot.model_copy(update={'inputs': snapshot.inputs.model_copy(update={'business': business}),
                                                       'reset': selected_reset})
                result = compute(selected, by_model[model], index)
                if result['issues']:
                    return compute(snapshot, cells, index)
                parts[model] = {field: result[field] for field in _fields}
                self._put(keys[model], parts[model])
        from .output_rules import rational, rate_display
        sources = [row for part in parts.values() for row in part['output_sources']]
        sources.sort(key=lambda row: (min(p.date for p in row.portions), row.kind != 'wip', row.input_date, row.id))
        source_by_id = {row.id: row for row in sources}
        attributions = [row for part in parts.values() for row in part['attributions']]
        attributions.sort(key=lambda row: (row.date, source_by_id[row.source_id].kind != 'wip',
                                           source_by_id[row.source_id].input_date, row.source_id))
        weekly = [row for part in parts.values() for row in part['weekly_results']]
        weekly.sort(key=lambda row: (row.week, row.model_code))
        demand_total = sum(row.demand for row in weekly)
        on_time = sum(row.on_time for row in weekly)
        totals = OutputTotals(computation_complete=True, expected=sum(p.quantity for s in sources for p in s.portions),
            outside_window=sum(p.quantity for s in sources for p in s.portions if p.date > snapshot.time.window_end),
            unallocated=sum(s.unallocated_output for s in sources), on_time=on_time,
            late=sum(row.late for row in weekly), pending=sum(row.pending for row in weekly), gap=sum(row.gap for row in weekly),
            on_time_rate=rational(Fraction(on_time, demand_total)) if demand_total else None,
            on_time_rate_display=rate_display(on_time, demand_total))
        return {'issues': [], 'reconciled_cells': cells, 'output_sources': sources, 'attributions': attributions,
                'weekly_results': weekly, 'confirmation_usage': [], 'output_totals': totals,
                'excluded_demands': [r for r in snapshot.inputs.business.demands
                                     if not snapshot.time.window_start <= r.week <= snapshot.time.window_end]}
