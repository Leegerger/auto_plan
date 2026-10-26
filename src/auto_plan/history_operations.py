"""Restore a server-owned immutable plan using current authoritative protection."""
from typing import Literal
from pydantic import Field
from .input_models import InputRow
from .plan_models import Plan
from .plan_operations import register_operation, ReplayResult
from .execution_rules import active_plan
from .history import resolve_history_reference


class HistoryReference(InputRow):
    kind: Literal['revision', 'adoption']
    id: int = Field(strict=True, ge=1)


class RestoreHistory(InputRow):
    kind: Literal['restore_history']
    source: HistoryReference


def restore_history(plan, command, context):
    from copy import deepcopy
    record = resolve_history_reference(context.history, command.source, context.time.as_of)
    from datetime import date
    restored = active_plan(Plan.model_validate(record['snapshot']['plan']), context.time, context.inputs, context.execution,
                           origin_start=date.fromisoformat(record['snapshot']['time']['window_start']))
    # All context and outstanding ledgers remain current. The shared replay and
    # service protect the returned plan and evaluate it with current inputs.
    lineage = {'history_restore': command.source.model_dump(mode='json')}
    source_goals = record.get('lineage', {}).get('demand_goals')
    if source_goals is not None:
        lineage['demand_goals'] = deepcopy(context.lineage.get('demand_goals', {})) | deepcopy(source_goals)
    return ReplayResult(restored, lineage=lineage)


register_operation('restore_history', RestoreHistory, restore_history)
