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


if __name__ == '__main__':
    import json
    print(json.dumps([{'case': case, **enumerate_case(case)} for case in CASES], indent=2))
