"""One Recipe event interpretation for validation and transaction scope."""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from .input_lookup import InputIndex
from .plan_models import Plan


@dataclass
class RecipeStates:
    initial_states: dict[str, str | None]
    endings: dict[tuple[date, str], str | None]
    lines: set[str]


def analyze_recipe_states(plan: Plan, index: InputIndex, problem=None) -> RecipeStates:
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
    endings = {}
    all_lines = set(events) | set(initial_states) | {row.line_code for row in plan.ordinary_idle}
    for line in sorted(all_lines):
        state = initial_states.get(line)
        pending = None
        seen_orders = set()
        for day, order, kind, item, position in sorted(events[line], key=lambda event: (event[0], event[1], event[2])):
            if (day, order) in seen_orders:
                problem('duplicate_order', '同日线体生产与切机的顺序编号必须唯一', day=day, line=line)
            seen_orders.add((day, order))
            if kind == 'production':
                if item.quantity or Fraction(item.hours):
                    recipe = index.models[item.model_code].recipe_code
                    if pending is not None:
                        problem('unfinished_switch', '跨日切机尚未完成，不能穿插生产', segment=item, recipe=recipe)
                    if state is not None and state != recipe:
                        problem('recipe_sequence', '生产前 Recipe 与线体状态不符，需要按有向关系完成切机', segment=item, recipe=recipe)
                    if state is None:
                        state = recipe
            else:
                if position == 0:
                    if pending is not None:
                        problem('unfinished_switch', '上一切机尚未完成，不能穿插另一切机', switch=item, day=day)
                    if state != item.from_recipe:
                        problem('recipe_sequence', '切机原 Recipe 与保留的线体状态不符', switch=item, day=day)
                    pending = item.id
                elif pending != item.id:
                    problem('switch_sequence', '同一切机的分配必须按顺序完成，不得穿插其他事件', switch=item, day=day)
                if position == len(item.allocations) - 1:
                    state = item.to_recipe
                    pending = None
            endings[(day, line)] = state
    return RecipeStates(initial_states, endings, all_lines)
