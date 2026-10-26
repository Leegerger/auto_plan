"""Independent demand alternatives from one prepared planning source."""
from fractions import Fraction
import json
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from .demand_trials import DemandTrial, trial_in_unit
from .exact import hours
from .plans import PlanCommand, PlanProblem


class DemandComparison(DemandTrial):
    mode: Literal['on_time'] = 'on_time'
    line_code: None = None


def affected_switches(result, prepared):
    phases = result['switch_effects']
    before = {row['id']: row for row in phases['before'] if row['line_code'] == result['request']['line_code']}
    after = {row['id']: row for row in phases['after'] if row['line_code'] == result['request']['line_code']}
    affected = {identity for identity in before.keys() | after.keys() if before.get(identity) != after.get(identity)}
    scope = result['request']['placement_scope']
    plans = [{row['id']: row for row in plan['switches'] if row['line_code'] == result['request']['line_code']}
             for plan in (prepared['plan'], result['preview']['plan'])]
    affected |= {identity for identity in plans[0].keys() | plans[1].keys() if plans[0].get(identity) != plans[1].get(identity)}
    affected |= {row['id'] for plan in plans for row in plan.values()
                 if any(scope['start_date'] <= allocation['date'] <= scope['end_date'] for allocation in row['allocations'])}
    return {phase: [row for identity, row in rows.items() if identity in affected]
            for phase, rows in (('before', before), ('after', after))}


def switch_totals(phases):
    return {phase: {field: hours(sum((Fraction(row[field]['exact']) for row in rows), Fraction())).model_dump(mode='json')
                    for field in ('total_hours', 'extra_hours')}
            for phase, rows in phases.items()}


def output_probe(model):
    """Use the same output rules on every source of the selected model.

    Capacity/protection still run on the complete plan. Other models cannot
    share its FIFO demand or confirmation quotas; final replay evaluates all.
    """
    from .output_rules import output_evaluation
    def evaluate(snapshot, index):
        segments = [row for row in snapshot.plan.segments if row.model_code == model]
        identities = {row.id for row in segments}
        business = snapshot.inputs.business.model_copy(update={
            'demands': [row for row in snapshot.inputs.business.demands if row.model_code == model],
            'confirmations': [row for row in snapshot.inputs.business.confirmations if row.model_code == model]})
        execution = snapshot.execution.model_copy(update={
            'facts': [row for row in snapshot.execution.facts if row.model_code == model],
            'association_cells': [row for row in snapshot.execution.association_cells if row.model_code == model],
            'associations': [row for row in snapshot.execution.associations if row.segment_id in identities]})
        selected = snapshot.model_copy(update={
            'inputs': snapshot.inputs.model_copy(update={'business': business}),
            'plan': snapshot.plan.model_copy(update={'segments': segments}), 'execution': execution,
            'reset': snapshot.reset.model_copy(update={'models': [row for row in snapshot.reset.models if row.model_code == model]}) if snapshot.reset else None})
        result = output_evaluation(selected, index, json_mode=True)
        return result | {'issues': [row.model_dump(mode='json') for row in result['issues']]}
    return evaluate


def comparison_in_unit(planner, unit, request, actor, now):
    command = PlanCommand.model_validate(request.model_dump(include=set(PlanCommand.model_fields)))
    prepared = planner._prepare(unit, command, now, actor, view=False)
    lines = {row['code']: row for row in prepared['snapshot']['inputs']['masters']['lines']}
    selected = request.scope.line_codes
    if not selected or any(code not in lines or not lines[code]['active'] for code in selected):
        raise PlanProblem(422, '比较范围须明确选择有效线体')
    rows = []
    for code in selected:
        candidate = DemandTrial.model_validate(request.model_dump() | {
            'line_code': code, 'action_id': str(uuid5(NAMESPACE_URL, json.dumps([request.action_id, code], ensure_ascii=False)))})
        result = trial_in_unit(planner, unit, candidate, actor, now, prepared=prepared,
                               probe_evaluation=output_probe(request.model_code), preview_view=False)
        switches = affected_switches(result, prepared)
        totals = None if result['status'] == 'incomplete' else switch_totals(switches)
        rows.append({key: value for key, value in result.items() if key not in ('preview', 'basis')} | {
            'line_code': code, 'line_name': lines[code]['name'], 'switch_totals': totals, 'switch_effects': switches})
    echoed = request.model_dump(mode='json', exclude=set(PlanCommand.model_fields) | {'line_code'})
    echoed['target_output'] = rows[0]['request']['target_output']
    return {'basis': result['basis'], 'request': echoed, 'rows': rows}
