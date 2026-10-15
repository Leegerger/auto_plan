"""Independent exhaustive oracle for two-source-date, two-model fixtures.

No application imports or shared solver constraints. Each tuple enumerates
integer input cells, aggregates cross-line quantities, rounds output, and
assigns each model's output to its earliest unsatisfied requirement. This
finite oracle only covers the stated CT-zero/shared-Recipe cases.
"""
from fractions import Fraction
from itertools import product


def enumerate_case(case):
    models, days, lines = range(2), range(2), range(case['lines'])
    cells = [(d, line, model) for d in days for line in lines for model in models]
    capacity, demand = case['capacity'], case['demand']
    yield_rate = Fraction(case['yield'])
    best, witnesses = None, []
    for quantities in product(range(capacity + 1), repeat=len(cells)):
        arrangement = dict(zip(cells, quantities))
        if any(sum(arrangement[d, line, model] for model in models) > capacity for d in days for line in lines):
            continue
        remaining = [list(row) for row in demand]
        ontime = [[0, 0], [0, 0]]
        late = [[0, 0], [0, 0]]
        invalid = False
        for day in days:
            for model in models:
                gross = sum(arrangement[day, line, model] for line in lines) * yield_rate
                output = -(-gross.numerator // gross.denominator)
                for week in days:
                    assigned = min(output, remaining[model][week])
                    if assigned and day < week and case.get('early_ban', False):
                        invalid = True
                    if assigned and day > week:
                        allowance = case.get('late_quota', 0)
                        covered = min(assigned, allowance)
                        late[model][week] += covered
                    elif assigned:
                        ontime[model][week] += assigned
                    output -= assigned
                    remaining[model][week] -= assigned
        if invalid:
            continue
        vector = [sum(demand[m][w] - ontime[m][w] for m in models) for w in days] + [0] * 13
        vector += [sum(demand[m][w] - ontime[m][w] - late[m][w] for m in models) for w in days] + [0] * 13
        vector += [0, 0, 0]
        if best is None or vector < best:
            best, witnesses = vector, [quantities]
        elif vector == best:
            witnesses.append(quantities)
    return {'objective': [str(v) for v in best], 'number_of_optimal_inputs': len(witnesses)}


CASES = [
    {'capacity': 1, 'lines': 1, 'yield': '1', 'demand': [[1, 1], [1, 0]]},
    {'capacity': 2, 'lines': 1, 'yield': '1', 'demand': [[2, 1], [1, 1]]},
    {'capacity': 1, 'lines': 2, 'yield': '1/2', 'demand': [[1, 1], [1, 0]]},
    {'capacity': 2, 'lines': 1, 'yield': '1/2', 'demand': [[1, 1], [1, 1]], 'early_ban': True},
    {'capacity': 1, 'lines': 1, 'yield': '1', 'demand': [[1, 0], [1, 1]], 'late_quota': 1},
]


def quota_case():
    best, inputs = None, []
    for quantity in range(2001):
        raw = Fraction(quantity * 931, 2000)
        portion = -(-raw.numerator // raw.denominator)
        first = min(2 * portion, 600)
        second = min(max(0, 2 * portion-first), 400)
        denominator = first+second
        shares = [Fraction(quantity*count, denominator) for count in (first, second)] if denominator else [Fraction(0), Fraction(0)]
        integer = [share.numerator//share.denominator for share in shares]
        extra = quantity-sum(integer) if denominator else 0
        for number in sorted(range(2), key=lambda n: (-(shares[n] % 1), n))[:extra]:
            integer[number] += 1
        if integer[1] > 428:
            continue
        vector = (600-min(portion, 600), 400-second, 600-first, 400-second)
        if best is None or vector < best:
            best, inputs = vector, [quantity]
        elif vector == best:
            inputs.append(quantity)
    return {'enumerated_inputs': [0, 2000], 'yield': '931/1000', 'ct': '1/2', 'demands': [600, 400],
        'material_second_week': 428, 'shipping_first_week': 1000,
        'objective_nonzero_week_coordinates': list(best), 'optimal_input_quantities': inputs,
        'eligible_minimum_input': min(inputs)}


if __name__ == '__main__':
    import argparse
    import json
    parser = argparse.ArgumentParser()
    parser.add_argument('--quota', action='store_true')
    args = parser.parse_args()
    print(json.dumps(quota_case() if args.quota else [{'case': case, **enumerate_case(case)} for case in CASES], indent=2))
