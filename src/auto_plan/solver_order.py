"""Recipe-state dynamic programming over bounded daily order neighborhoods."""
from collections import defaultdict
from itertools import permutations
from .recipe_states import analyze_recipe_states


def _relations(rows):
    codes = [r.model_code for r in sorted(rows, key=lambda r: (r.order, r.id)) if r.quantity]
    return {(left, right) for n, left in enumerate(codes) for right in codes[n+1:] if left != right}


def order_proposal(search, plan):
    if search.request.mode == 'new_week':
        return plan, False
    groups = defaultdict(list)
    for row in plan.segments:
        if search.scope.start_date <= row.date <= search.scope.end_date and row.line_code in search.scope.line_codes:
            groups[row.line_code, row.date].append(row)
    adopted = defaultdict(list)
    if search.snapshot.baseline:
        for row in search.snapshot.baseline.plan.segments:
            adopted[row.line_code, row.date].append(row)
    states = analyze_recipe_states(search.snapshot.plan, search.index)
    replacements, exhaustive = {}, True
    for line in sorted(search.scope.line_codes):
        incoming = states.initial_states.get(line)
        for (day, code), state in sorted(states.endings.items()):
            if code == line and day < search.scope.start_date:
                incoming = state
        # Each state retains a path and a lexicographic switch/order cost.
        frontier = {incoming: ((0, 0), [])}
        for (code, day), rows in sorted(groups.items()):
            if code != line:
                continue
            search.check()
            original = sorted(rows, key=lambda r: (r.order, r.id))
            recipes = {search.index.models[r.model_code].recipe_code for r in rows}
            if len(rows) <= 6:
                variants = permutations(original)
            else:
                exhaustive = False
                variants = [original, sorted(original, key=lambda r: (search.index.models[r.model_code].recipe_code, r.order))]
                variants += [[r for recipe in order for r in original if search.index.models[r.model_code].recipe_code == recipe]
                    for order in ([*sorted(recipes)], [*reversed(sorted(recipes))])]
            old_relations = _relations(adopted[code, day])
            common = {r.model_code for r in rows if r.quantity} & {r.model_code for r in adopted[code, day] if r.quantity}
            next_frontier = {}
            for sequence in variants:
                ordered = [r.model_copy(update={'order': n * 2 + 1}) for n, r in enumerate(sequence)]
                # Excluded models are fixed at their original event positions.
                if any(not search.scope.contains(r) and r.order != original_row.order
                       for r, original_row in zip(ordered, sequence)):
                    continue
                rel = _relations(ordered)
                reversals = sum((a, b) not in old_relations and (b, a) in old_relations
                    for a, b in rel if a in common and b in common)
                for entering, (cost, path) in frontier.items():
                    state, switches = entering, 0
                    for row in ordered:
                        if not row.quantity:
                            continue
                        recipe = search.index.models[row.model_code].recipe_code
                        switches += state is not None and state != recipe
                        state = recipe
                    proposed = (cost[0] + switches, cost[1] + reversals)
                    if state not in next_frontier or proposed < next_frontier[state][0]:
                        next_frontier[state] = proposed, [*path, *ordered]
            if not next_frontier:
                return plan, False
            frontier = next_frontier
        if not frontier:
            continue
        # Boundary state must match when later protected work exists.
        later = any(r.line_code == line and r.date > search.scope.end_date for r in plan.segments)
        ending = incoming
        for (day, code), state in sorted(states.endings.items()):
            if code == line and day <= search.scope.end_date:
                ending = state
        feasible = [value for state, value in frontier.items() if not later or state == ending]
        if feasible:
            _, path = min(feasible, key=lambda value: value[0])
            replacements.update({r.id: r for r in path})
    return plan.model_copy(update={'segments': [replacements.get(r.id, r) for r in plan.segments]}), exhaustive
