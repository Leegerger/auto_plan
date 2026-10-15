"""Shared plan service: authoritative replay, dependency checks and explicit adoption."""
from collections import defaultdict
from datetime import datetime
from typing import Any
from uuid import UUID
from pydantic import Field
from .input_models import InputRow, Pieces, InputSnapshot
from .plan_models import (Plan, EvaluationSnapshot, EvaluationTime, ExecutionContext, ResetContext,
                          EvaluationPreferences, ExecutionCoverage, PlanCell, AdoptedBaseline)
from .plan_operations import replay_result, OperationContext
from .rules import evaluate
from .input_lookup import InputIndex
from .recipe_states import analyze_recipe_states


class PlanningContext(InputRow):
    execution: ExecutionContext = Field(default_factory=ExecutionContext)
    locks: list[PlanCell] = Field(default_factory=list)
    reset: ResetContext | None = None
    coverage: list[ExecutionCoverage] = Field(default_factory=list)
    preferences: EvaluationPreferences = Field(default_factory=EvaluationPreferences)
    lineage: dict[str, Any] = Field(default_factory=dict)


class PlanCommand(InputRow):
    base_revision: Pieces
    input_version: Pieces
    latest_adoption: Pieces
    operations: list[dict[str, Any]] = Field(default_factory=list, max_length=10000)


class SubmitPlan(PlanCommand):
    operation_id: UUID
    pending: bool = False


class InitializePlan(SubmitPlan):
    inputs: InputSnapshot
    context: PlanningContext
    plan: Plan
    lineage: dict[str, Any] = Field(default_factory=dict)


class PlanProblem(Exception):
    def __init__(self, status, detail, **data):
        self.status, self.detail, self.data = status, detail, data


class PlanBusy(Exception):
    pass


class PlanUnknown(Exception):
    pass


def cells(plan):
    result = defaultdict(int)
    for row in plan.segments:
        result[(row.date.isoformat(), row.line_code, row.model_code)] += row.quantity
    return dict(result)


def differences(before, after, path=()):
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        for key in sorted(before.keys() | after.keys()):
            result += differences(before.get(key), after.get(key), path+(key,))
        return result
    if isinstance(before,list) and isinstance(after,list) and all(isinstance(row,dict) and 'id' in row for row in before+after):
        left,right={r['id']:r for r in before},{r['id']:r for r in after}
        if len(left)==len(before) and len(right)==len(after):
            return differences(left,right,path)
    return [] if before == after else [{'path': list(path), 'before': before, 'after': after}]


def plan_differences(before,after):
    changes=differences(before,after)
    segments={r['id']:r for r in before.get('segments',[])+after.get('segments',[])}
    labels={'date':'生产日','line_code':'线体','model_code':'机种','order':'日内顺序','quantity':'投入数量','hours':'生产工时',
            'segments':'生产段','switches':'切机安排','initial_states':'起始 Recipe','ordinary_idle':'普通空闲','allocations':'切机日分配'}
    for change in changes:
        path=change['path']; row=segments.get(path[1],{}) if len(path)>1 and path[0]=='segments' else {}
        change.update(date=row.get('date'),line_code=row.get('line_code'),model_code=row.get('model_code'),field=labels.get(path[-1],labels.get(path[0],'计划安排')))
    return changes


def effect_keys(before, after, context):
    """Conservative CAP/CELL/REQ dependency closure from complete frozen plans.

    Recipe state affects the changed line until before/after state converges,
    including empty dates. A model's sources share FIFO and quotas, so all its demand weeks and
    all lines on each changed source date depend on the operation.
    """
    old, new = cells(before), cells(after)
    keys, changed_models, source_dates, changed_lines = set(), set(), defaultdict(set), defaultdict(set)
    old_rows = {r.id:r.model_dump(mode='json') for r in before.segments}
    new_rows = {r.id:r.model_dump(mode='json') for r in after.segments}
    touched = [row for identity in old_rows.keys() | new_rows.keys() if old_rows.get(identity) != new_rows.get(identity)
               for row in (old_rows.get(identity), new_rows.get(identity)) if row]
    for row in touched:
        day,line,model = row['date'],row['line_code'],row['model_code']
        changed_models.add(model); source_dates[model].add(day); changed_lines[line].add(day)
    for key in old.keys() | new.keys():
        if old.get(key,0) != new.get(key,0):
            day,line,model=key
            changed_models.add(model); source_dates[model].add(day); changed_lines[line].add(day)
    for field in ('switches','ordinary_idle','initial_states'):
        left, right = getattr(before,field),getattr(after,field)
        if left == right:
            continue
        identity = lambda row: row.id if field == 'switches' else (row.line_code if field == 'initial_states' else (row.line_code,row.date))
        a,b={identity(r):r for r in left},{identity(r):r for r in right}
        for ident in a.keys()|b.keys():
            if a.get(ident)==b.get(ident): continue
            for row in (a.get(ident),b.get(ident)):
                if row is None: continue
                dates = [r.date.isoformat() for r in row.allocations] if field=='switches' else [row.date.isoformat()] if field=='ordinary_idle' else [context.time.window_start.isoformat()]
                changed_lines[row.line_code].update(dates)
    all_segments=before.segments+after.segments
    from datetime import timedelta
    # Propagate only while the before/after Recipe state differs. Quantity or
    # hour-only edits with identical state leave later independent days free.
    index=InputIndex(context.inputs)
    before_states=analyze_recipe_states(before,index)
    after_states=analyze_recipe_states(after,index)
    def states(plan,analysis,line):
        initial=next((r.recipe_code for r in plan.initial_states if r.line_code==line),None)
        events={day.isoformat():recipe for (day,event_line),recipe in analysis.endings.items() if event_line==line}
        return initial,events
    for line, dates in changed_lines.items():
        for day in dates: keys.add(('CAP',line,day))
        first=min(dates)
        old_state,old_events=states(before,before_states,line)
        new_state,new_events=states(after,after_states,line)
        day=context.time.window_start
        while day<=context.time.window_end:
            iso=day.isoformat()
            entering_different=old_state is not None and new_state is not None and old_state!=new_state
            if iso in old_events and old_events[iso] is not None: old_state=old_events[iso]
            if iso in new_events and new_events[iso] is not None: new_state=new_events[iso]
            if iso>=first and (entering_different or (old_state is not None and new_state is not None and old_state!=new_state)):
                keys.add(('CAP',line,iso))
            day+=timedelta(days=1)
    lines={r.code for r in context.inputs.masters.lines}|{r.line_code for r in all_segments}
    for model in changed_models:
        for day in source_dates[model]:
            for line in lines: keys.add(('CELL',day,line,model))
        for demand in context.inputs.business.demands:
            if demand.model_code==model: keys.add(('REQ',model,demand.week.isoformat()))
        for batch in context.inputs.business.confirmations:
            if batch.model_code==model: keys.add(('REQ',model,batch.week.isoformat()))
    return sorted(keys)


def result_effect_keys(before, after, snapshot):
    keys=set()
    for attribute in ('unallocated','operation_issues'):
        old={entry['id']:entry for entry in before.get(attribute,[])}
        new={entry['id']:entry for entry in after.get(attribute,[])}
        touched=[entry for identity in old.keys()|new.keys() if old.get(identity)!=new.get(identity)
                 for entry in (old.get(identity),new.get(identity)) if entry]
        for entry in touched:
            models=[entry['model_code']] if entry.get('model_code') else [r.code for r in snapshot.inputs.masters.models]
            for model in models:
                explicit=entry.get('demand_week') or entry.get('week')
                weeks={str(explicit)} if explicit else {r.week.isoformat() for r in snapshot.inputs.business.demands if r.model_code==model}|{r.week.isoformat() for r in snapshot.inputs.business.confirmations if r.model_code==model}
                for week in weeks or {snapshot.time.window_start.isoformat()}:
                    keys.add(('REQ',model,week))
    return keys


def protect(before, after, snapshot):
    old,new=cells(before),cells(after)
    failures=[]
    for lock in snapshot.locks:
        key=(lock.date.isoformat(),lock.line_code,lock.model_code)
        if new.get(key,0)!=lock.quantity:
            failures.append({'code':'locked_cell','date':key[0],'line_code':key[1],'model_code':key[2],'quantity':lock.quantity})
    # Executed pieces stay on their original segment identities and positions.
    facts={(r.date.isoformat(),r.line_code,r.model_code):r.quantity for r in snapshot.execution.facts}
    by_id={s.id:s for s in after.segments}
    assigned=defaultdict(int)
    for segment in sorted(before.segments,key=lambda s:(s.date,s.line_code,s.order,s.id)):
        key=(segment.date.isoformat(),segment.line_code,segment.model_code)
        executed=min(segment.quantity,max(0,facts.get(key,0)-assigned[key])); assigned[key]+=executed
        if not executed: continue
        target=by_id.get(segment.id)
        if target is None or (target.date,target.line_code,target.model_code)!=(segment.date,segment.line_code,segment.model_code) or target.quantity<executed:
            failures.append({'code':'executed_segment','segment_id':segment.id,'date':key[0],'line_code':key[1],'model_code':key[2],'quantity':executed})
    new_assigned=defaultdict(int)
    new_execution={}
    for segment in sorted(after.segments,key=lambda s:(s.date,s.line_code,s.order,s.id)):
        key=(segment.date.isoformat(),segment.line_code,segment.model_code)
        amount=min(segment.quantity,max(0,facts.get(key,0)-new_assigned[key]))
        new_assigned[key]+=amount; new_execution[segment.id]=amount
    assigned=defaultdict(int)
    for segment in sorted(before.segments,key=lambda s:(s.date,s.line_code,s.order,s.id)):
        key=(segment.date.isoformat(),segment.line_code,segment.model_code)
        amount=min(segment.quantity,max(0,facts.get(key,0)-assigned[key])); assigned[key]+=amount
        if amount and new_execution.get(segment.id,0)!=amount:
            failures.append({'code':'executed_association','segment_id':segment.id,'date':key[0],
                             'line_code':key[1],'model_code':key[2],'quantity':amount})
    if failures: raise PlanProblem(409,'操作会改变已执行部分或锁定数量',conflicts=failures)


class PlanService:
    def __init__(self, store):
        self.store=store

    def _snapshot(self, unit, plan, now):
        context=unit.context
        baseline=None
        if unit.adoption:
            record=unit.get_adoption(unit.adoption)
            baseline=AdoptedBaseline(version=unit.adoption,plan=record['snapshot']['plan'],coverage=record['coverage'])
        return EvaluationSnapshot(time=EvaluationTime.at(now),inputs=unit.inputs,plan=plan,
            execution=context.execution,reset=context.reset,locks=context.locks,baseline=baseline,preferences=context.preferences)

    def _prepare(self, unit, command, now):
        if command.input_version!=unit.inputs.input_version:
            try: old=unit.get_inputs(command.input_version).model_dump(mode='json')
            except KeyError: old={}
            raise PlanProblem(409,'输入资料已变化，请查看差异并重新预览',input_version=unit.inputs.input_version,
                              input_diff=differences(old,unit.inputs.model_dump(mode='json')) + differences(
                                  unit.get_context(command.input_version).model_dump(mode='json'),
                                  unit.context.model_dump(mode='json'),('context',)),
                              context=unit.context.model_dump(mode='json'))
        try: base=unit.get_revision(command.base_revision)
        except KeyError: raise PlanProblem(409,'基准修订不存在或已超出恢复范围',revision=unit.revision) from None
        if command.base_revision>unit.revision: raise PlanProblem(409,'基准修订不存在',revision=unit.revision)
        base_plan=Plan.model_validate(base['snapshot']['plan'])
        snapshot=self._snapshot(unit,base_plan,now)
        try:
            original_result=replay_result(base_plan,command.operations,OperationContext(snapshot,command.base_revision,base.get('unallocated',[]),base.get('operation_issues',[]),base.get('lineage',{})))
            proposed=original_result.plan
        except ValueError as error: raise PlanProblem(422,str(error)) from None
        effects=set(map(tuple,effect_keys(base_plan,proposed,snapshot)))
        effects|=result_effect_keys(base,{'unallocated':original_result.unallocated,'operation_issues':original_result.issues},snapshot)
        changed=unit.effects_since(command.base_revision)
        intersection=effects&changed
        if intersection:
            raise PlanProblem(409,'其他用户已修改关联范围，请核对差异',revision=unit.revision,
                              conflicts=sorted(intersection),changes=plan_differences(base['snapshot']['plan'],unit.plan.model_dump(mode='json')))
        try:
            current=unit.get_revision(unit.revision)
            replayed=replay_result(unit.plan,command.operations,OperationContext(
                self._snapshot(unit,unit.plan,now),command.base_revision,current.get('unallocated',[]),
                current.get('operation_issues',[]),current.get('lineage',{})))
            merged=replayed.plan
        except ValueError as error: raise PlanProblem(409,str(error)) from None
        merged_snapshot=self._snapshot(unit,merged,now)
        effects|=set(map(tuple,effect_keys(unit.plan,merged,merged_snapshot)))
        effects|=result_effect_keys(current,{'unallocated':replayed.unallocated,'operation_issues':replayed.issues},merged_snapshot)
        if effects&changed:
            raise PlanProblem(409,'合并后的关联范围已变化',revision=unit.revision,conflicts=sorted(effects&changed))
        protect(unit.plan,merged,merged_snapshot)
        result=evaluate(merged_snapshot)
        return {'revision':unit.revision,'input_version':unit.inputs.input_version,'latest_adoption':unit.adoption,
                'plan':merged.model_dump(mode='json'),'snapshot':merged_snapshot.model_dump(mode='json'),
                'evaluation':result.model_dump(mode='json'),'effects':[list(k) for k in sorted(effects)],
                'changes':plan_differences(unit.plan.model_dump(mode='json'),merged.model_dump(mode='json')),
                'unallocated':replayed.unallocated, 'operation_issues':replayed.issues,
                'adoption_blocked':any(issue.get('blocks_adoption',issue.get('severity')=='error') for issue in replayed.issues), 'merged':command.base_revision!=unit.revision,
                'coverage':[r.model_dump(mode='json') for r in unit.context.coverage],
                'parent_revision':unit.revision, 'base_revision':command.base_revision,
                'lineage':unit.get_revision(unit.revision).get('lineage',{})|unit.context.lineage|replayed.lineage}

    def preview(self, command, actor, now):
        return self.store.read(actor,lambda unit:self._prepare(unit,command,now))

    def submit(self, command, actor, now, kind='save'):
        def action(unit):
            if kind=='adopt' and command.latest_adoption!=unit.adoption:
                raise PlanProblem(409,'采用版已变化，请重新核对',latest_adoption=unit.adoption,revision=unit.revision)
            prepared=self._prepare(unit,command,now)
            if kind=='adopt' and (not prepared['evaluation']['executable'] or prepared['adoption_blocked']):
                raise PlanProblem(422,'存在硬冲突、未完成计算或必要确认，不能采用',evaluation=prepared['evaluation'])
            if kind=='save' and not command.pending and (not prepared['evaluation']['executable'] or prepared['adoption_blocked']):
                raise PlanProblem(422,'请明确保存为待处理草稿',evaluation=prepared['evaluation'])
            return unit.append(prepared,command.operations,actor,now,kind,command.pending if kind=='save' else False)
        return self.store.submit(command,actor,now,kind,action)

    def initialize(self, command: InitializePlan, actor, now):
        def action(unit):
            if unit.revision or unit.adoption or command.base_revision or command.latest_adoption:
                raise PlanProblem(409,'已有共享计划，不能重复首次接入')
            if command.input_version!=unit.inputs.input_version:
                raise PlanProblem(409,'输入资料已变化，请重新预览首次接入',input_version=unit.inputs.input_version)
            return self.store.initialize_in_transaction(unit.cursor,actor,now,command.inputs,
                command.context,command.plan,command.lineage,command.operation_id)
        return self.store.submit(command,actor,now,'initialize',action)

    def current(self, actor, now):
        def current(unit):
            snapshot=self._snapshot(unit,unit.plan,now)
            record=unit.get_revision(unit.revision)
            return {'revision':unit.revision,'latest_adoption':unit.adoption,'input_version':unit.inputs.input_version,
                    'pending':record.get('pending',False), 'kind':record.get('kind','empty'),
                    'unallocated':record.get('unallocated',[]), 'operation_issues':record.get('operation_issues',[]),
                    'lineage':record.get('lineage',{})|unit.context.lineage, 'coverage':record.get('coverage',[]),
                    'plan':unit.plan.model_dump(mode='json'),'context':unit.context.model_dump(mode='json'),
                    'evaluation':evaluate(snapshot).model_dump(mode='json')}
        return self.store.read(actor,current)

    def revision(self, identity, actor):
        return self.store.read(actor,lambda unit:unit.get_revision(identity))

    def adoption(self, identity, actor):
        return self.store.read(actor,lambda unit:unit.get_adoption(identity))

    def verify(self, operation_id, actor, now):
        return self.store.verify(str(operation_id),actor,now)
