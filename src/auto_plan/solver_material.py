"""Exact integer largest-remainder input shares and dated material batch quotas."""
from collections import defaultdict


def material_constraints(search, variables, constraint, variable, sources):
    batches = search.snapshot.inputs.business.confirmations
    uses = defaultdict(list)

    def equality(coefficients, value=0):
        constraint(coefficients, lower=value, upper=value)

    def constant(value):
        column = variable(value)
        equality({column: 1}, value)
        return column

    def multiply(left, left_upper, right, right_upper):
        if left_upper > right_upper:
            left, right, left_upper, right_upper = right, left, right_upper, left_upper
        product = variable(left_upper * right_upper)
        if not left_upper or not right_upper:
            return product
        bits, products = {}, {}
        for position in range(left_upper.bit_length()):
            bit, selected = variable(1), variable(right_upper)
            bits[bit] = 2**position
            products[selected] = 2**position
            constraint({selected: 1, bit: -right_upper}, upper=0)
            constraint({selected: 1, right: -1}, upper=0)
            constraint({selected: 1, right: -1, bit: -right_upper}, lower=-right_upper)
        equality({left: 1, **{key: -value for key, value in bits.items()}})
        equality({product: 1, **{key: -value for key, value in products.items()}})
        return product

    for identity, source in sources.items():
        search.check()
        if source['wip'] or not any(week > source['input_date'] for week in source['allocations']):
            continue
        allocations = {}
        for week, columns in source['allocations'].items():
            total = variable(sum(variables[column]['upper'] for column in columns))
            equality({total: 1, **{column: -1 for column in columns}})
            allocations[week] = total
        quantity = variable(source['quantity_upper'])
        equality({quantity: 1, **{column: -coefficient for column, coefficient in source['quantity_columns'].items()}}, source['fixed_quantity'])
        qmax = source['quantity_upper']
        maximum = sum(variables[column]['upper'] for column in allocations.values())
        if source['unit_free']:
            # A free, one-portion, unit-yield source cannot retain unallocated
            # input under candidate eligibility. Its exact shares equal output.
            equality({quantity: 1, **{column: -1 for column in allocations.values()}})
            shares = allocations
        else:
            denominator = variable(maximum)
            equality({denominator: 1, **{column: -1 for column in allocations.values()}})
            active = variable(1)
            constraint({denominator: 1, active: -maximum}, upper=0)
            constraint({denominator: 1, active: -1}, lower=0)
            effective_quantity = multiply(quantity, qmax, active, 1)
            floors, remainders, used = {}, {}, {}
            for week, output in allocations.items():
                amount = multiply(effective_quantity, qmax, output, variables[output]['upper'])
                floor = variable(qmax)
                multiplied = multiply(floor, qmax, denominator, maximum)
                remainder = variable(max(0, maximum-1))
                equality({amount: 1, multiplied: -1, remainder: -1})
                constraint({remainder: 1, denominator: -1, active: 1}, upper=0)
                constraint({floor: 1, active: -qmax}, upper=0)
                present = variable(1)
                constraint({output: 1, present: -variables[output]['upper']}, upper=0)
                constraint({output: 1, present: -1}, lower=0)
                floors[week], remainders[week], used[week] = floor, remainder, present
            leftover = variable(max(0, len(allocations)-1))
            equality({leftover: 1, effective_quantity: -1, **{column: 1 for column in floors.values()}})
            shares = {}
            for week in allocations:
                predecessors = []
                for other in allocations:
                    if week == other:
                        continue
                    precedes = variable(1)
                    threshold, bound = (0 if other < week else 1), maximum + 1
                    coefficients = {remainders[other]: 1, remainders[week]: -1, precedes: -bound}
                    constraint(coefficients, lower=threshold-bound, upper=threshold-1)
                    actual = variable(1)
                    for operand in (precedes, used[week], used[other]):
                        constraint({actual: 1, operand: -1}, upper=0)
                    constraint({actual: 1, precedes: -1, used[week]: -1, used[other]: -1}, lower=-2)
                    predecessors.append(actual)
                bonus = variable(1)
                constraint({bonus: 1, used[week]: -1}, upper=0)
                bound = len(allocations) + 1
                constraint({**{column: 1 for column in predecessors}, leftover: -1, bonus: bound}, upper=bound-1)
                constraint({**{column: 1 for column in predecessors}, leftover: -1, bonus: bound, used[week]: -bound}, lower=-bound)
                share = variable(qmax)
                equality({share: 1, floors[week]: -1, bonus: -1})
                shares[week] = share
        for week, share in shares.items():
            if source['input_date'] >= week:
                continue
            matching = []
            for batch in batches:
                if batch.model_code == source['model'] and batch.week == week and batch.kind == 'material' and batch.status == 'allowed' and batch.date <= source['input_date']:
                    amount = variable(min(qmax, batch.quantity))
                    matching.append(amount)
                    uses[batch.model_code, batch.week, batch.batch_id].append(amount)
            equality({share: 1, **{column: -1 for column in matching}})
    for batch in batches:
        key = batch.model_code, batch.week, batch.batch_id
        if batch.kind == 'material' and batch.status == 'allowed' and key in uses:
            constraint({column: 1 for column in uses[key]}, upper=batch.quantity)
