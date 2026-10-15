"""Fresh exec entry point. Import exactly one native solver in each process."""
import json
import math
import sys
from fractions import Fraction
from pathlib import Path
from threading import Event, Thread


def _watch_stop(data, interrupt):
    finished = Event()
    def watch():
        while not finished.wait(0.05):
            if Path(data['cancel_file']).exists():
                interrupt()
                return
    Thread(target=watch, daemon=True).start()
    return finished


def _stopped(data):
    return Path(data['cancel_file']).exists()


def _integers(values):
    numbers = [Fraction(v) for v in values]
    denominator = math.lcm(*(v.denominator for v in numbers))
    result = [v.numerator * (denominator // v.denominator) for v in numbers]
    if any(abs(v) >= 2**60 for v in result):
        raise ValueError('Exact native coefficients exceed the safe integer range')
    return result, denominator


def cp_sat(data):
    from ortools.sat.python import cp_model
    model = cp_model.CpModel()
    choices = [model.new_bool_var(f'p{n}') for n in range(len(data['objectives']))]
    model.add_exactly_one(choices)
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = data['threads']
    solver.parameters.max_time_in_seconds = data['seconds'] / 33
    solver.parameters.log_search_progress = False
    finished = _watch_stop(data, solver.stop_search)
    stages = []
    for layer in range(33):
        if _stopped(data):
            return {'status': 'interrupted', 'stages': stages}
        coefficients, denominator = _integers([values[layer] for values in data['objectives']])
        expression = sum(c * v for c, v in zip(coefficients, choices))
        model.minimize(expression)
        status = solver.solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return {'status': solver.status_name(status), 'stages': stages}
        chosen = next(n for n, variable in enumerate(choices) if solver.value(variable))
        optimum = coefficients[chosen]
        stages.append({'layer': layer, 'value': str(Fraction(optimum, denominator)),
                       'proven': status == cp_model.OPTIMAL})
        if status != cp_model.OPTIMAL:
            return {'status': 'feasible', 'selected': chosen, 'stages': stages}
        model.add(expression == optimum)
    finished.set()
    return {'status': 'optimal_in_table', 'selected': chosen, 'stages': stages}


def quantity_cp_sat(data):
    from ortools.sat.python import cp_model
    model = cp_model.CpModel()
    columns = [model.new_int_var(row['lower'], row['upper'], f'q{n}') for n, row in enumerate(data['model']['variables'])]
    for row in data['model']['constraints']:
        keys, raw = list(row['coefficients']), list(row['coefficients'].values())
        bounds = [row[k] for k in ('lower', 'upper') if row.get(k) is not None]
        integers, denominator = _integers([*raw, *bounds])
        expression = sum(value * columns[int(key)] for key, value in zip(keys, integers))
        position = len(raw)
        for bound in ('lower', 'upper'):
            if row.get(bound) is not None:
                model.add(expression >= integers[position] if bound == 'lower' else expression <= integers[position])
                position += 1
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = data['threads']
    solver.parameters.max_time_in_seconds = data['seconds'] / max(1, len(data['model']['objectives']))
    finished = _watch_stop(data, solver.stop_search)
    stages, values = [], None
    for layer, raw in enumerate(data['model']['objectives']):
        if _stopped(data):
            return {'status': 'interrupted', 'stages': stages}
        keys = list(raw)
        coefficients, denominator = _integers(raw.values())
        expression = sum(c * columns[int(key)] for key, c in zip(keys, coefficients))
        model.minimize(expression)
        status = solver.solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            break
        values = [solver.value(variable) for variable in columns]
        value = sum(c * values[int(key)] for key, c in zip(keys, coefficients))
        stages.append({'layer': layer, 'value': str(Fraction(value, denominator)), 'proven': status == cp_model.OPTIMAL})
        if status != cp_model.OPTIMAL:
            break
        model.add(expression == value)
    finished.set()
    return {'status': solver.status_name(status), 'values': values, 'stages': stages}


def quantity_highs(data):
    import highspy
    import numpy as np
    solver = highspy.Highs()
    solver.setOptionValue('output_flag', False)
    solver.setOptionValue('threads', data['threads'])
    solver.setOptionValue('time_limit', data['seconds'] / max(1, len(data['model']['objectives'])))
    solver.HandleUserInterrupt = True
    finished = _watch_stop(data, solver.cancelSolve)
    variables = data['model']['variables']
    count = len(variables)
    indices = np.arange(count, dtype=np.int32)
    solver.addVars(count, np.array([v['lower'] for v in variables], dtype=np.float64), np.array([v['upper'] for v in variables], dtype=np.float64))
    solver.changeColsIntegrality(count, indices, np.full(count, highspy.HighsVarType.kInteger, dtype=np.uint8))
    for row in data['model']['constraints']:
        keys = np.array([int(key) for key in row['coefficients']], dtype=np.int32)
        values = np.array([float(Fraction(v)) for v in row['coefficients'].values()], dtype=np.float64)
        solver.addRow(float(Fraction(row['lower'])) if row.get('lower') is not None else -highspy.kHighsInf,
            float(Fraction(row['upper'])) if row.get('upper') is not None else highspy.kHighsInf, len(keys), keys, values)
    stages, values = [], None
    for layer, raw in enumerate(data['model']['objectives']):
        if _stopped(data):
            return {'status': 'interrupted', 'stages': stages}
        costs = np.zeros(count)
        for key, value in raw.items():
            costs[int(key)] = float(Fraction(value))
        solver.changeColsCost(count, indices, costs)
        solver.run()
        solution, status = solver.getSolution(), solver.getModelStatus()
        if not solution.value_valid:
            break
        values = [round(value) for value in solution.col_value]
        value = sum(Fraction(coefficient) * values[int(key)] for key, coefficient in raw.items())
        stages.append({'layer': layer, 'value': str(value), 'proven': status == highspy.HighsModelStatus.kOptimal})
        if status != highspy.HighsModelStatus.kOptimal:
            break
        solver.addRow(float(value), float(value), count, indices, costs)
    finished.set()
    return {'status': str(status), 'values': values, 'stages': stages}


def highs(data):
    import highspy
    import numpy as np
    solver = highspy.Highs()
    solver.setOptionValue('output_flag', False)
    solver.setOptionValue('threads', data['threads'])
    solver.setOptionValue('time_limit', data['seconds'] / 33)
    solver.HandleUserInterrupt = True
    finished = _watch_stop(data, solver.cancelSolve)
    count = len(data['objectives'])
    solver.addVars(count, np.zeros(count), np.ones(count))
    indices = np.arange(count, dtype=np.int32)
    solver.changeColsIntegrality(count, indices, np.full(count, highspy.HighsVarType.kInteger, dtype=np.uint8))
    solver.addRow(1, 1, count, indices, np.ones(count))
    stages = []
    for layer in range(33):
        if _stopped(data):
            return {'status': 'interrupted', 'stages': stages}
        coefficients, denominator = _integers([values[layer] for values in data['objectives']])
        values = np.array(coefficients, dtype=np.float64)
        solver.changeColsCost(count, indices, values)
        solver.run()
        status, solution = solver.getModelStatus(), solver.getSolution()
        if not solution.value_valid:
            return {'status': str(status), 'stages': stages}
        chosen = max(range(count), key=lambda n: solution.col_value[n])
        # HiGHS is numerical. The controller rechecks the exact table choice;
        # numerical optimality is never published as an exact business bound.
        optimum = coefficients[chosen]
        stages.append({'layer': layer, 'value': str(Fraction(optimum, denominator)),
                       'proven': status == highspy.HighsModelStatus.kOptimal})
        if status != highspy.HighsModelStatus.kOptimal:
            return {'status': 'feasible', 'selected': chosen, 'stages': stages}
        solver.addRow(optimum, optimum, count, indices, values)
    finished.set()
    return {'status': 'optimal_in_table', 'selected': chosen, 'stages': stages}


def main():
    try:
        data = json.load(sys.stdin)
        if data['route'] not in ('cp_sat', 'highs'):
            raise ValueError('Unknown native route')
        if 'model' in data:
            if not 1 <= len(data['model']['variables']) <= 100000:
                raise ValueError('Native variable limit exceeded')
            result = quantity_cp_sat(data) if data['route'] == 'cp_sat' else quantity_highs(data)
        else:
            if not 1 <= len(data['objectives']) <= 4096:
                raise ValueError('Native table size exceeded')
            result = cp_sat(data) if data['route'] == 'cp_sat' else highs(data)
        print(json.dumps(result, separators=(',', ':')))
    except Exception as error:
        print(json.dumps({'status': 'failed', 'error': type(error).__name__ + ': ' + str(error)[:1000]}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
