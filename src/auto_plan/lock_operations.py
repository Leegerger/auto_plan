"""Explicit aggregate locks and preferences in the ordered business action queue."""
from typing import Literal
from pydantic import Field
from .input_models import InputRow, Code, ProductionDate
from .plan_models import PlanCell, EvaluationPreferences
from .plan_operations import register_operation, ReplayResult


class CellIdentity(InputRow):
    date: ProductionDate
    line_code: Code
    model_code: Code


class LockCells(InputRow):
    kind: Literal['lock_cells']
    cells: list[PlanCell] = Field(min_length=1,max_length=10000)


class UnlockCells(InputRow):
    kind: Literal['unlock_cells']
    cells: list[CellIdentity] = Field(min_length=1,max_length=10000)


class SetPreferences(InputRow):
    kind: Literal['set_preferences']
    preferences: EvaluationPreferences


def identity(row): return row.date,row.line_code,row.model_code


def validate_cells(command,context):
    from .input_lookup import InputIndex
    index=InputIndex(context.inputs)
    seen=set()
    for row in command.cells:
        if identity(row) in seen: raise ValueError('批量锁定或解锁包含重复单元格')
        seen.add(identity(row))
        if command.kind=='lock_cells' and not context.time.window_start<=row.date<=context.time.window_end:
            raise ValueError('新锁定的生产日必须位于当周至第十五周')
        if row.line_code not in index.lines or row.model_code not in index.models:
            raise ValueError('锁定或解锁引用不存在的线体或机种')


def lock_cells(plan,command,context):
    validate_cells(command,context)
    from .plans import cells
    quantities=cells(plan)
    locks={identity(row):row for row in context.locks}
    for row in command.cells:
        key=identity(row)
        if key in locks and locks[key].quantity!=row.quantity:
            raise ValueError('更改锁定数量须先显式解锁')
        if quantities.get((row.date.isoformat(),row.line_code,row.model_code),0)!=row.quantity:
            raise ValueError('锁定数量必须等于当前日期、线体、机种的汇总投入量')
        locks[key]=row
    return ReplayResult(plan,context_updates={'locks':list(locks.values())})


def unlock_cells(plan,command,context):
    validate_cells(command,context)
    removed={identity(row) for row in command.cells}
    current={identity(row):row for row in context.locks}
    if removed-current.keys(): raise ValueError('待解锁单元格已变化，请重新核对')
    return ReplayResult(plan,context_updates={'locks':[row for key,row in current.items() if key not in removed]})


def set_preferences(plan,command,context):
    return ReplayResult(plan,context_updates={'preferences':command.preferences})


register_operation('lock_cells',LockCells,lock_cells)
register_operation('unlock_cells',UnlockCells,unlock_cells)
register_operation('set_preferences',SetPreferences,set_preferences)
