"""Typed atomic replay operations. Business editors register commands here."""
from typing import Literal
from dataclasses import dataclass, field
from copy import deepcopy
import hashlib
import json
from pydantic import Field, model_validator, TypeAdapter
from .input_models import InputRow, Code
from .plan_models import Plan, ProductionSegment, RecipeSwitch, InitialRecipeState, OrdinaryIdle, CurrentDayWitness


class ReplacePlan(InputRow):
    kind: Literal['replace_plan']
    plan: Plan


class PatchPlan(InputRow):
    current_day_witnesses: list[CurrentDayWitness] = Field(default_factory=list)
    kind: Literal['patch_plan']
    segments: list[ProductionSegment] = Field(default_factory=list)
    remove_segments: list[Code] = Field(default_factory=list)
    switches: list[RecipeSwitch] = Field(default_factory=list)
    remove_switches: list[Code] = Field(default_factory=list)
    initial_states: list[InitialRecipeState] = Field(default_factory=list)
    ordinary_idle: list[OrdinaryIdle] = Field(default_factory=list)
    remove_idle: list[OrdinaryIdle] = Field(default_factory=list)


    @model_validator(mode='after')
    def unambiguous(self):
        for rows,removed,identity in (
            (self.segments,self.remove_segments,lambda r:r.id),
            (self.switches,self.remove_switches,lambda r:r.id),
            (self.initial_states,[],lambda r:r.line_code),
            (self.ordinary_idle,[(r.date,r.line_code) for r in self.remove_idle],lambda r:(r.date,r.line_code))):
            ids=[identity(r) for r in rows]
            if len(ids)!=len(set(ids)) or len(removed)!=len(set(removed)) or set(ids)&set(removed):
                raise ValueError('同一操作包含重复或相互矛盾的识别组合')
        return self


OPERATIONS = {}


def register_operation(kind, model, replay):
    if kind in OPERATIONS:
        raise ValueError('Operation already registered')
    OPERATIONS[kind] = (model, replay)


def replace(plan, command, context):
    return command.plan.model_copy(deep=True)


def patch(plan, command, context):
    def merged(old, new, removed, identity):
        values = {identity(row): row for row in old if identity(row) not in removed}
        values.update({identity(row): row for row in new})
        return list(values.values())
    return Plan(
        current_day_witnesses=merged(plan.current_day_witnesses,command.current_day_witnesses,set(),lambda r:(r.date,r.line_code)),
        segments=merged(plan.segments, command.segments, set(command.remove_segments), lambda row: row.id),
        switches=merged(plan.switches, command.switches, set(command.remove_switches), lambda row: row.id),
        initial_states=merged(plan.initial_states, command.initial_states, set(), lambda row: row.line_code),
        ordinary_idle=merged(plan.ordinary_idle, command.ordinary_idle,
            {(row.date,row.line_code) for row in command.remove_idle}, lambda row: (row.date,row.line_code)))


register_operation('replace_plan', ReplacePlan, replace)
register_operation('patch_plan', PatchPlan, patch)


@dataclass
class OperationContext:
    snapshot: object
    source_revision: int = 0
    unallocated: list[dict] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    lineage: dict = field(default_factory=dict)
    action_id: str = ''
    actor: str = ''
    history: object = None
    latest_adoption: int = 0
    current_revision: int = 0
    allow_past_input: bool = False

    def __getattr__(self,name):
        return getattr(self.snapshot,name)


@dataclass
class ReplayResult:
    plan: Plan
    unallocated: list[dict] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    lineage: dict = field(default_factory=dict)
    resolve_unallocated: list[str] = field(default_factory=list)
    resolve_issues: list[str] = field(default_factory=list)
    replace_unallocated: bool = False
    replace_issues: bool = False
    context_updates: dict = field(default_factory=dict)


def replay_result(plan, operations, context):
    inherited=context if isinstance(context,OperationContext) else OperationContext(context)
    result=ReplayResult(plan,deepcopy(inherited.unallocated),deepcopy(inherited.issues),deepcopy(inherited.lineage))
    for ordinal,raw in enumerate(operations):
        if raw.get('kind') not in OPERATIONS:
            raise ValueError('未知计划操作')
        content=json.dumps(raw,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        if 'action_id' in raw: TypeAdapter(Code).validate_python(raw['action_id'])
        action=raw.get('action_id') or hashlib.sha256(f'{inherited.source_revision}:{ordinal}:{content}'.encode()).hexdigest()[:32]
        operation_context=OperationContext(inherited.snapshot.model_copy(update={'plan':result.plan,**result.context_updates}),
            inherited.source_revision,deepcopy(result.unallocated),deepcopy(result.issues),deepcopy(result.lineage),action,inherited.actor,inherited.history,inherited.latest_adoption,inherited.current_revision,inherited.allow_past_input)
        model, apply = OPERATIONS[raw['kind']]
        value = apply(result.plan, model.model_validate({key:value for key,value in raw.items() if key!='action_id'}), operation_context)
        candidate=Plan.model_validate(value.plan if isinstance(value,ReplayResult) else value)
        if candidate!=result.plan:
            from .plans import protect
            protect(result.plan,candidate,operation_context.snapshot,allow_past_input=inherited.allow_past_input)
        if isinstance(value, ReplayResult):
            if set(value.context_updates)-{'locks','preferences','execution','reset'}:
                raise ValueError('未知计划上下文变更')
            result.context_updates.update(value.context_updates)
            result.plan=candidate
            for attribute,resolved,replace in [('unallocated',value.resolve_unallocated,value.replace_unallocated),('issues',value.resolve_issues,value.replace_issues)]:
                old=getattr(result,attribute)
                if set(resolved)-{entry['id'] for entry in old}:
                    raise ValueError('待处理结果已变化，请重新核对')
                entries={} if replace else {entry['id']:entry for entry in old if entry['id'] not in resolved}
                for index,entry in enumerate(getattr(value,attribute)):
                    entry=deepcopy(entry)
                    entry.setdefault('action_id',action)
                    entry.setdefault('id',f'{action}:{attribute}:{index}')
                    entries[entry['id']]=entry
                setattr(result,attribute,list(entries.values()))
            result.lineage.update(value.lineage)
        else:
            result.plan=Plan.model_validate(value)
    identities=[row.id for row in result.plan.segments]+[row.id for row in result.plan.switches]
    if len(identities)!=len(set(identities)):
        raise ValueError('生产段和切机识别号必须唯一')
    return result


def replay(plan, operations, context):
    return replay_result(plan,operations,context).plan

# Load typed business commands after the replay primitives are defined.
from . import manual_operations  # noqa: E402,F401
from . import quantity_operations  # noqa: E402,F401
from . import lock_operations  # noqa: E402,F401

from . import execution_operations  # noqa: E402,F401

from . import reset_operations  # noqa: E402,F401
