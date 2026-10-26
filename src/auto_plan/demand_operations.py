"""Accepted qualified-output intent, replayed through ordinary input placement."""
from copy import deepcopy
from datetime import date
from typing import Literal
from pydantic import Field, model_validator
from .input_models import InputRow, Code, Pieces
from .editing_scope import EditingScope
from .manual_operations import AppendFill, append_fill
from .plan_operations import OperationContext, ReplayResult, register_operation


class DemandGoal(InputRow):
    id: Code
    model_code: Code
    demand_week: date
    requested_output: Pieces
    baseline_attributed: Pieces
    desired_attributed: Pieces
    origin_revision: Pieces
    origin_input_version: Pieces


class DemandPlacement(InputRow):
    date: date
    quantity: int = Field(strict=True, gt=0)


class SupplementDemand(InputRow):
    kind: Literal['supplement_demand']
    model_code: Code
    demand_week: date
    mode: Literal['on_time'] = 'on_time'
    line_code: Code
    start_date: date
    end_date: date
    scope: EditingScope
    goal: DemandGoal
    placements: list[DemandPlacement] = Field(default_factory=list, max_length=105)

    @model_validator(mode='after')
    def coherent_intent(self):
        if self.demand_week.weekday() or self.goal.demand_week != self.demand_week or self.goal.model_code != self.model_code:
            raise ValueError('补排目标须关联同一机种及周一需求周')
        if not self.goal.requested_output or self.goal.desired_attributed != self.goal.baseline_attributed + self.goal.requested_output:
            raise ValueError('补排目标须保留原始合格产出目标及归属基准')
        if not self.scope.start_date <= self.start_date <= self.end_date <= self.scope.end_date or self.scope.line_codes != [self.line_code]:
            raise ValueError('补排须保留所选日期和单线调整范围')
        if self.scope.model_codes and self.model_code not in self.scope.model_codes:
            raise ValueError('补排机种不在调整范围')
        if len({row.date for row in self.placements}) != len(self.placements) or any(not self.start_date <= row.date <= self.end_date for row in self.placements):
            raise ValueError('补排接受的逐日投入不可重复或超出原日期范围')
        return self


def place_input(plan, snapshot, scope, model, line, day, quantity, action_id):
    command = AppendFill(kind='append_fill', scope=scope, model_code=model, line_code=line,
                         start_date=day, end_date=day, quantity=quantity)
    context = OperationContext(snapshot.model_copy(update={'plan': plan}), action_id=f'{action_id}:{day}')
    return append_fill(plan, command, context)


def refresh_demand_goals(lineage, evaluation, time):
    """Refresh output balances from the final ledger, never accepted deltas."""
    if 'demand_goals' not in lineage:
        return lineage
    refreshed = deepcopy(lineage)
    for goal in refreshed['demand_goals'].values():
        week = date.fromisoformat(goal['demand_week'])
        row = next((row for row in evaluation['weekly_results'] if row['model_code'] == goal['model_code'] and row['week'] == goal['demand_week']), None)
        unavailable = not time.window_start <= week <= time.window_end
        incomplete = row is not None and not row['computation_complete']
        quantity = None if unavailable or incomplete else max(min(goal['desired_attributed'], row['demand'] if row else 0) - (row['attributed'] if row else 0), 0)
        goal.update(status='unavailable' if unavailable else 'incomplete' if incomplete else 'pending' if quantity else 'complete',
                    current_demand=row['demand'] if row else None if unavailable else 0,
                    current_attributed=None if unavailable or incomplete else row['attributed'] if row else 0,
                    remaining_output_target=quantity)
        for field in ('on_time', 'late', 'pending_material', 'pending_shipping'):
            goal[field] = None if unavailable or incomplete else row[field] if row else 0
    return refreshed


def supplement_demand(plan, command, context):
    goals = deepcopy(context.lineage.get('demand_goals', {}))
    existing = goals.get(command.goal.id)
    if existing is not None and context.action_id in existing.get('accepted_actions', {}):
        raise ValueError('此补排动作已经接受，请勿重复安排')
    for identity, goal in goals.items():
        if identity != command.goal.id and (goal['model_code'], goal['demand_week']) == (command.model_code, command.demand_week.isoformat()) and goal.get('remaining_output_target', 1) != 0:
            raise ValueError('此机种需求周已有补排目标，请保留原目标处理')
    result = plan
    actual = []
    for row in sorted(command.placements, key=lambda row: row.date):
        before = {segment.id for segment in result.segments}
        placed = place_input(result, context.snapshot, command.scope, command.model_code, command.line_code,
                             row.date, row.quantity, context.action_id)
        result = placed.plan
        actual.extend(segment.model_dump(mode='json') for segment in result.segments if segment.id not in before)
    record = command.goal.model_dump(mode='json') | {'mode': command.mode, 'accepted_actions': deepcopy((existing or {}).get('accepted_actions', {}))}
    record['accepted_actions'][context.action_id] = {
        'line_code': command.line_code, 'start_date': command.start_date.isoformat(), 'end_date': command.end_date.isoformat(),
        'mode': command.mode, 'placements': [row.model_dump(mode='json') for row in command.placements],
        'actual_placements': actual, 'suggested_input': sum(row['quantity'] for row in actual)}
    goals[command.goal.id] = record
    return ReplayResult(result, lineage={'demand_goals': goals})


register_operation('supplement_demand', SupplementDemand, supplement_demand)
