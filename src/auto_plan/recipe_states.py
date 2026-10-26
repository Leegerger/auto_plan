"""One Recipe event interpretation for validation and transaction scope."""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from fractions import Fraction
from .input_lookup import InputIndex
from .plan_models import Plan


@dataclass
class RecipeTransition:
    date: date
    order: int
    kind: str
    identity: str
    entering_recipe: str | None
    ending_recipe: str | None
    pending_switch: str | None


@dataclass
class RecipeStates:
    initial_states: dict[str, str | None]
    endings: dict[tuple[date, str], str | None]
    lines: set[str]
    transitions: dict[str, list[RecipeTransition]] = field(default_factory=dict)
    day_starts: dict[tuple[date, str], str | None] = field(default_factory=dict)
    initial_pending: dict[str, str] = field(default_factory=dict)


def analyze_recipe_states(plan: Plan, index: InputIndex, problem=None, *, include_transitions=False, confirmed_states=(), window_start=None) -> RecipeStates:
    """Walk ordered events, retaining the state across every empty date.

    A scope comparison needs these states without output attribution or capacity
    DTOs. Validation supplies its issue callback to this same event walk.
    """
    if problem is None:
        def problem(*args, **kwargs):
            pass
    initial_states = {row.line_code: row.recipe_code for row in plan.initial_states}
    events = defaultdict(list)
    for segment in plan.segments:
        if segment.model_code in index.models and segment.line_code in index.lines:
            events[segment.line_code].append((segment.date, segment.order, 'production', segment, None))
    for switch in plan.switches:
        for position, allocation in enumerate(sorted(switch.allocations, key=lambda row: (row.date, row.order))):
            events[switch.line_code].append((allocation.date, allocation.order, 'switch', switch, position))
    from .production_time import production_date, production_cutoff
    day_starts = {}
    initial_jobs = {}
    initial_records = {}
    ordered = sorted(confirmed_states, key=lambda row: (row.effective_at,getattr(row,'priority',1),row.recorded_at,row.id))
    unique = {(row.line_code,row.effective_at):row for row in ordered}
    for record in unique.values():
        if window_start is not None and record.effective_at <= production_cutoff(window_start):
            initial_states[record.line_code] = record.recipe_code
            initial_jobs[record.line_code] = getattr(record, 'unfinished_switch', None)
            initial_records[record.line_code] = record.id
            continue
        day = production_date(record.effective_at)
        if record.effective_at == production_cutoff(day) or getattr(record, 'unfinished_switch', None) is not None:
            order = -1
            day_starts[day, record.line_code] = record.recipe_code
        else:
            order = max((event[1] for event in events[record.line_code] if event[0] == day), default=-1) + 1
        events[record.line_code].append((day, order, 'confirmation', record, None))
    endings = {}
    transitions = defaultdict(list)
    all_lines = set(events) | set(initial_states) | {row.line_code for row in plan.ordinary_idle}
    for line in sorted(all_lines):
        state = initial_states.get(line)
        actual = initial_jobs.get(line)
        actual_record = initial_records.get(line)
        pending = actual.job_id if actual is not None else None
        seen_orders = set()
        for day, order, kind, item, position in sorted(events[line], key=lambda event: (event[0], event[1], event[2])):
            entering_recipe = state
            if (day, order) in seen_orders:
                problem('duplicate_order', '同日线体生产与切机的顺序编号必须唯一', day=day, line=line)
            seen_orders.add((day, order))
            if kind == 'confirmation':
                state = item.recipe_code
                actual = getattr(item, 'unfinished_switch', None)
                actual_record = item.id
                pending = actual.job_id if actual is not None else None
            elif kind == 'production':
                if item.quantity or Fraction(item.hours):
                    recipe = index.models[item.model_code].recipe_code
                    if pending is not None:
                        problem('unfinished_switch', '跨日切机尚未完成，不能穿插生产', segment=item, recipe=recipe)
                    if state is not None and state != recipe:
                        problem('recipe_sequence', '生产前 Recipe 与线体状态不符，需要按有向关系完成切机', segment=item, recipe=recipe)
                    if state is None and pending is None:
                        state = recipe
            else:
                residual = item.residual_record_id is not None
                continuing = residual and actual is not None and pending == item.id == actual.job_id and item.residual_record_id == actual_record and (item.from_recipe,item.to_recipe) == (actual.from_recipe,actual.to_recipe)
                blocked = actual is not None and not continuing
                if position == 0:
                    if pending is not None and not continuing:
                        problem('unfinished_switch', '上一切机尚未完成，不能穿插另一切机', switch=item, day=day)
                    if not continuing and state != item.from_recipe:
                        problem('recipe_sequence', '切机原 Recipe 与保留的线体状态不符', switch=item, day=day)
                    if not blocked:
                        pending = item.id
                elif pending != item.id:
                    problem('switch_sequence', '同一切机的分配必须按顺序完成，不得穿插其他事件', switch=item, day=day)
                complete = not blocked and (not residual or continuing and sum((Fraction(row.hours) for row in item.allocations),Fraction(0)) == Fraction(actual.remaining_hours))
                if position == len(item.allocations) - 1 and complete:
                    state = item.to_recipe
                    pending = None
                    actual = None
            endings[(day, line)] = state
            if include_transitions:
                transitions[line].append(RecipeTransition(day, order, kind, item.id, entering_recipe, state, pending))
    return RecipeStates(initial_states, endings, all_lines, dict(transitions), day_starts,
                        {line:job.job_id for line,job in initial_jobs.items() if job is not None})
