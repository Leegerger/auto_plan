"""Finite local arrangements include zero quantities, placements and orders.

The finite domain is explicit in each stage proof. Larger business inputs keep
using construction and Recipe repair rather than inheriting this search cap.
"""
from collections import defaultdict
from datetime import timedelta
from fractions import Fraction
from itertools import permutations, product
from math import factorial
from .solver import objective
from .solver_witness import fixed_switch_certificate


def enumerate_local(search, plan, *, recipe=None, maximum=512):
    models = sorted({r.model_code for r in search.snapshot.inputs.business.demands if r.quantity
        and search.snapshot.time.window_start <= r.week <= search.snapshot.time.window_end})
    models = [m for m in models if m in search.index.models and search.index.models[m].active
        and search.index.recipes[search.index.models[m].recipe_code].active
        and (recipe is None or search.index.models[m].recipe_code == recipe)
        and (not search.scope.model_codes or m in search.scope.model_codes)]
    fixed, templates = [], {}
    for row in plan.segments:
        if not search.mutable(row) or search.executed.get(row.id) or recipe is not None and (row.date <= search.today or search.index.models[row.model_code].recipe_code != recipe) or search.request.mode == 'new_week' and row.id in search.original_ids:
            fixed.append(row)
        else:
            templates.setdefault((row.date, row.line_code, row.model_code), row)
    variables, count = [], 1
    day = max(search.scope.start_date, search.today + timedelta(days=1) if recipe is not None else search.today)
    while day <= search.scope.end_date:
        for line in search.scope.line_codes:
            if not search.capacity_allowed(day, line) or line not in search.index.lines or not search.index.lines[line].active or not search.index.day(day, line)['available_hours']:
                continue
            for model in models:
                if (day, line, model) in search.locks:
                    continue
                code = search.index.models[model].recipe_code
                capacity = search.index.capacity(day.strftime('%Y-%m'), code, line)
                parameters = search.index.critical_parameters(day.strftime('%Y-%m'), model)
                if capacity['state'] != 'known' or any(p['state'] == 'missing' for p in parameters.values()):
                    continue
                yield_rate = Fraction(parameters['line_yield']['percent']) * Fraction(parameters['model_yield']['percent']) / 10000
                if not yield_rate:
                    continue
                demand = sum(r.quantity for r in search.snapshot.inputs.business.demands if r.model_code == model)
                # Ceil output never needs more than the unrounded input bound.
                upper = min(capacity['pieces'], -(-(Fraction(demand) / yield_rate).numerator // (Fraction(demand) / yield_rate).denominator))
                count *= upper + 1
                if count > maximum:
                    return None, f'domain exceeds {maximum} quantity combinations'
                variables.append(((day, line, model), range(upper + 1)))
        day += timedelta(days=1)
    if not variables:
        return [], 'no free quantities'
    table, scores, seen = [], [], set()
    for amounts in product(*(v[1] for v in variables)):
        search.check()
        rows = list(fixed)
        daily = defaultdict(list)
        for row in fixed:
            daily[row.date, row.line_code].append(row)
        for (key, _), quantity in zip(variables, amounts):
            if not quantity:
                continue
            day, line, model = key
            row = templates.get(key)
            if row is None:
                row = next(r for r in search.add(plan.model_copy(update={'segments': []}), model, day, line, quantity).segments)
            row = row.model_copy(update={'quantity': quantity})
            daily[day, line].append(row)
        arrangements, alternatives = [], 1
        for key, values in sorted(daily.items()):
            if not any(search.scope.contains(r) for r in values):
                arrangements.append([values])
                continue
            if recipe is not None:
                arrangements.append([sorted(values, key=lambda r: (r.order, r.id))])
                continue
            if len(values) > 6:
                return None, 'daily order domain exceeds six events, using scalable order repair'
            alternatives *= factorial(len(values))
            if alternatives * count > maximum * 4:
                return None, 'combined quantity/order domain exceeds bounded local search'
            arrangements.append(permutations(values))
        for sequences in product(*arrangements):
            search.check()
            selected = []
            for sequence in sequences:
                for number, row in enumerate(sequence):
                    selected.append(row.model_copy(update={'order': 2 * number + 1}) if recipe is None and search.scope.contains(row) else row)
            trial = plan.model_copy(update={'segments': selected})
            try:
                if recipe is not None:
                    trial = fixed_switch_certificate(trial, search.context, search.scope,
                        search.request.mode == 'new_week', recipe_code=recipe)
                    # Fixed global switch work and all other Recipe quantities
                    # remain byte-for-byte identical in this neighborhood.
                    if trial.switches != plan.switches:
                        continue
                else:
                    trial = search.witness(trial)
                search.authorize(trial)
            except Exception as error:
                from .certificate import CertificateError
                from .plans import PlanProblem
                if not isinstance(error, (ValueError, CertificateError, PlanProblem)):
                    raise
                search.scope_issue(error)
                continue
            from .rules import evaluate
            result = evaluate(search.snapshot.model_copy(update={'plan': trial}))
            trial_valid = result.capacity_valid and result.computation_complete and not any(issue.severity == 'error' for issue in result.issues)
            if not trial_valid or not search.eligible(trial):
                continue
            score = objective(result)
            identity = (tuple(score), tuple((r.date, r.line_code, r.model_code, r.quantity, r.order) for r in trial.segments))
            if identity in seen:
                continue
            seen.add(identity)
            table.append(trial)
            scores.append(score)
    return (table, scores), f'{count} quantity combinations with all bounded daily orders'
