"""Shared plan service: authoritative replay, dependency checks and explicit adoption."""
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID
from pydantic import Field
from .input_models import InputRow, Pieces, InputSnapshot
from .plan_models import (Plan, EvaluationSnapshot, EvaluationTime, ExecutionContext, ResetContext,
                          EvaluationPreferences, ExecutionCoverage, PlanCell, AdoptedBaseline)
from .plan_operations import replay_result, OperationContext
from .exact import hours
from .production_time import week_label
from .input_lookup import InputIndex
from .recipe_states import analyze_recipe_states


def plan_hour_display(plan):
    return {'segments':{row.id:hours(row.hours).model_dump(mode='json') for row in plan.segments},
            'switches':{row.id:[{'hours':hours(a.hours).model_dump(mode='json'),'idle_hours':hours(a.idle_hours).model_dump(mode='json')} for a in row.allocations] for row in plan.switches}}


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
    old_proofs={(r.date,r.line_code):r for r in before.current_day_witnesses}
    new_proofs={(r.date,r.line_code):r for r in after.current_day_witnesses}
    for proof_key in old_proofs.keys()|new_proofs.keys():
        if old_proofs.get(proof_key)!=new_proofs.get(proof_key):
            keys.add(('CAP',proof_key[1],proof_key[0].isoformat()))
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


def context_effect_keys(context,updates,snapshot=None):
    keys=set()
    if 'locks' in updates:
        before={(r.date.isoformat(),r.line_code,r.model_code):r.quantity for r in context.locks}
        after={(r.date.isoformat(),r.line_code,r.model_code):r.quantity for r in updates['locks']}
        for key in before.keys()|after.keys():
            if before.get(key)!=after.get(key): keys.add(('CELL',*key))
    if snapshot is not None:
        from .production_time import production_date
        models=set();dated=set();lines={r.code for r in snapshot.inputs.masters.lines}
        if 'execution' in updates:
            before=context.execution;after=updates['execution']
            old={(r.date,r.line_code,r.model_code):r.quantity for r in before.facts}
            new={(r.date,r.line_code,r.model_code):r.quantity for r in after.facts}
            for cell in old.keys()|new.keys():
                if old.get(cell)!=new.get(cell): dated.add(cell);models.add(cell[2])
            if (before.coverage,before.cutoff,before.associations,before.association_cells)!=(after.coverage,after.cutoff,after.associations,after.association_cells):
                dated.update((r.date,r.line_code,r.model_code) for r in snapshot.plan.segments if r.date<=production_date(snapshot.time.as_of))
                for coverage in before.coverage+after.coverage:
                    day=max(coverage.start_date,snapshot.time.window_start)
                    while day<=min(coverage.end_date,production_date(snapshot.time.as_of),snapshot.time.window_end):
                        dated.update((day,line,model) for line in coverage.line_codes for model in coverage.model_codes)
                        day+=timedelta(days=1)
                models.update(cell[2] for cell in dated)
            a={(r.date,r.line_code):r for r in before.day_capacities};b={(r.date,r.line_code):r for r in after.day_capacities}
            for cell in a.keys()|b.keys():
                if a.get(cell)!=b.get(cell): keys.add(('CAP',cell[1],cell[0].isoformat()))
        if 'reset' in updates:
            before=context.reset;after=updates['reset']
            a={r.model_code:r for r in before.models} if before else {};b={r.model_code:r for r in after.models} if after else {}
            models.update(model for model in a.keys()|b.keys() if a.get(model)!=b.get(model))
        for day,line,model in dated:
            keys.add(('CAP',line,day.isoformat()))
            for related_line in lines: keys.add(('CELL',day.isoformat(),related_line,model))
        for model in models:
            weeks={snapshot.time.window_start+timedelta(days=7*n) for n in range(15)}
            weeks.update(r.week for r in snapshot.inputs.business.demands if r.model_code==model)
            weeks.update(r.week for r in snapshot.inputs.business.confirmations if r.model_code==model)
            keys.update(('REQ',model,week.isoformat()) for week in weeks)
    return keys


def protect(before, after, snapshot, *, allow_past_input=False):
    from .execution_rules import executed_by_segment
    from .production_time import production_date
    old,new=cells(before),cells(after)
    today=production_date(snapshot.time.as_of)
    failures=[]
    if not allow_past_input:
        for key,quantity in new.items():
            if snapshot.time.window_start.isoformat()<=key[0]<today.isoformat() and quantity>old.get(key,0):
                failures.append({'code':'past_input','date':key[0],'line_code':key[1],'model_code':key[2],'quantity':quantity-old.get(key,0)})
    for lock in snapshot.locks:
        if not snapshot.time.window_start<=lock.date<=snapshot.time.window_end: continue
        key=(lock.date.isoformat(),lock.line_code,lock.model_code)
        if new.get(key,0)!=lock.quantity:
            failures.append({'code':'locked_cell','date':key[0],'line_code':key[1],'model_code':key[2],'quantity':lock.quantity})
    before_execution=executed_by_segment(before,snapshot.execution)
    after_execution=executed_by_segment(after,snapshot.execution)
    by_id={s.id:s for s in after.segments}
    for segment in before.segments:
        if not snapshot.time.window_start<=segment.date<=snapshot.time.window_end: continue
        executed=before_execution.get(segment.id,0)
        if not executed: continue
        target=by_id.get(segment.id)
        where={'segment_id':segment.id,'date':segment.date.isoformat(),'line_code':segment.line_code,'model_code':segment.model_code,'quantity':executed}
        if target is None or (target.date,target.line_code,target.model_code)!=(segment.date,segment.line_code,segment.model_code) or target.quantity<executed:
            failures.append({'code':'executed_segment',**where})
        elif after_execution.get(segment.id,0)!=executed:
            failures.append({'code':'executed_association',**where})
    for day,line in {(r.date,r.line_code) for r in before.segments if before_execution.get(r.id,0) and snapshot.time.window_start<=r.date<=snapshot.time.window_end}:
        prior=[r.id for r in sorted(before.segments,key=lambda r:(r.order,r.id)) if r.date==day and r.line_code==line and before_execution.get(r.id,0)]
        current=[r.id for r in sorted(after.segments,key=lambda r:(r.order,r.id)) if r.id in set(prior)]
        if prior!=current: failures.append({'code':'executed_order','date':day.isoformat(),'line_code':line,'segment_ids':prior})
    if failures: raise PlanProblem(409,'操作会改变已执行部分、锁定数量或在过去日新增投入',conflicts=failures)


def execution_segments(plan,execution):
    from .execution_rules import executed_by_segment
    assigned=executed_by_segment(plan,execution)
    return [{'segment_id':r.id,'executed':assigned.get(r.id,0),'remaining':max(0,r.quantity-assigned.get(r.id,0))}
            for r in sorted(plan.segments,key=lambda r:(r.date,r.line_code,r.order,r.id))]


def execution_surplus(plan,execution):
    from .execution_rules import executed_by_segment
    assigned=executed_by_segment(plan,execution)
    used=defaultdict(int)
    for row in plan.segments: used[row.date,row.line_code,row.model_code]+=assigned.get(row.id,0)
    return [row.model_copy(update={'quantity':row.quantity-used[row.date,row.line_code,row.model_code]}).model_dump(mode='json')
            for row in execution.facts if row.quantity>used[row.date,row.line_code,row.model_code]]


class PlanService:
    def __init__(self, store):
        self.store=store

    def _evaluation_json(self,snapshot):
        from .rules import _evaluation_json
        return _evaluation_json(snapshot,output_cache=getattr(self.store,'output_cache',None))

    @staticmethod
    def _planning_plan(unit,record):
        value=getattr(unit,'planning_plan_value',None)
        return value(record) if value is not None else record['snapshot']['plan']

    def _snapshot(self, unit, plan, now):
        from .execution_rules import freeze_associations
        context=unit.context
        baseline=None
        if unit.adoption:
            record=unit.get_planning_adoption(unit.adoption)
            baseline=AdoptedBaseline(version=unit.adoption,plan=self._planning_plan(unit,record),coverage=record['coverage'])
        return EvaluationSnapshot(time=EvaluationTime.at(now),inputs=unit.inputs,plan=plan,
            execution=freeze_associations(unit.plan,context.execution),reset=context.reset,locks=context.locks,baseline=baseline,preferences=context.preferences)

    def _reset_participants(self,unit,snapshot,lineage):
        from .reset_operations import participating_models
        def selected(number):
            source=snapshot.baseline.plan if snapshot.baseline and number==unit.adoption else Plan.model_validate(unit.get_planning_adoption(number)['snapshot']['plan']) if number else Plan()
            return {'source_adoption':number,'model_codes':sorted(participating_models(snapshot.inputs,source,snapshot.execution,snapshot.time.window_start))}
        record=lineage.get('resets',{}).get(snapshot.time.window_start.isoformat())
        return {'current':selected(unit.adoption),'correction':selected(record['source_adoption']) if record and record.get('effective_adoption') else None}

    def _prepare(self, unit, command, now, actor=''):
        if command.input_version!=unit.inputs.input_version:
            try: old=unit.get_inputs(command.input_version).model_dump(mode='json')
            except KeyError: old={}
            raise PlanProblem(409,'输入资料已变化，请查看差异并重新预览',input_version=unit.inputs.input_version,
                              input_diff=differences(old,unit.inputs.model_dump(mode='json')) + differences(
                                  unit.get_context(command.input_version).model_dump(mode='json'),
                                  unit.context.model_dump(mode='json'),('context',)),
                              context=unit.context.model_dump(mode='json'))
        initial_import=unit.revision==0 and bool(unit.context.lineage.get('external_reset'))
        try: base=unit.get_planning_revision(command.base_revision)
        except KeyError: raise PlanProblem(409,'基准修订不存在或已超出恢复范围',revision=unit.revision) from None
        if command.base_revision>unit.revision: raise PlanProblem(409,'基准修订不存在',revision=unit.revision)
        base_plan=Plan.model_validate(self._planning_plan(unit,base))
        snapshot=self._snapshot(unit,base_plan,now)
        try:
            original_result=replay_result(base_plan,command.operations,OperationContext(snapshot,command.base_revision,base.get('unallocated',[]),base.get('operation_issues',[]),base.get('lineage',{})|unit.context.lineage,actor=actor,history=unit,latest_adoption=unit.adoption,current_revision=unit.revision,allow_past_input=initial_import))
            proposed=original_result.plan
        except ValueError as error: raise PlanProblem(422,str(error)) from None
        if snapshot.execution!=unit.context.execution:
            original_result.context_updates.setdefault('execution',snapshot.execution)
        effects=set(map(tuple,effect_keys(base_plan,proposed,snapshot)))
        effects|=context_effect_keys(unit.context,original_result.context_updates,snapshot)
        effects|=result_effect_keys(base,{'unallocated':original_result.unallocated,'operation_issues':original_result.issues},snapshot)
        changed=unit.effects_since(command.base_revision)
        intersection=effects&changed
        if intersection:
            raise PlanProblem(409,'其他用户已修改关联范围，请核对差异',revision=unit.revision,
                              conflicts=sorted(intersection),changes=plan_differences(base['snapshot']['plan'],unit.plan.model_dump(mode='json')))
        try:
            current=unit.get_planning_revision(unit.revision)
            # Inputs, adopted baseline, association freeze and time are shared
            # by both replays in this pinned unit; only the plan differs.
            if command.base_revision==unit.revision:
                # This immutable revision and its replay context are identical
                # to the first replay. Merged-context effects still run below.
                replayed=original_result
            else:
                replayed=replay_result(unit.plan,command.operations,OperationContext(
                    snapshot.model_copy(update={'plan':unit.plan}),command.base_revision,current.get('unallocated',[]),
                    current.get('operation_issues',[]),current.get('lineage',{})|unit.context.lineage,actor=actor,history=unit,latest_adoption=unit.adoption,current_revision=unit.revision,allow_past_input=initial_import))
            merged=replayed.plan
        except ValueError as error: raise PlanProblem(409,str(error)) from None
        if snapshot.execution!=unit.context.execution:
            replayed.context_updates.setdefault('execution',snapshot.execution)
        merged_context=unit.context.model_copy(update=replayed.context_updates)
        if {'execution','reset'}&replayed.context_updates.keys():
            merged_context=merged_context.model_copy(update={'lineage':unit.context.lineage|replayed.lineage})
        merged_snapshot=snapshot.model_copy(update={'plan':merged,**replayed.context_updates})
        from .reset_operations import refresh_lineage
        replayed.lineage=refresh_lineage(replayed.lineage,merged,merged_snapshot)
        if {'execution','reset'}&replayed.context_updates.keys():
            merged_context=merged_context.model_copy(update={'lineage':unit.context.lineage|replayed.lineage})
        effects|=set(map(tuple,effect_keys(unit.plan,merged,merged_snapshot)))
        effects|=result_effect_keys(current,{'unallocated':replayed.unallocated,'operation_issues':replayed.issues},merged_snapshot)
        if effects&changed:
            raise PlanProblem(409,'合并后的关联范围已变化',revision=unit.revision,conflicts=sorted(effects&changed))
        protect(unit.plan,merged,merged_snapshot,allow_past_input=initial_import)
        result=self._evaluation_json(merged_snapshot)
        return {'revision':unit.revision,'input_version':unit.inputs.input_version,'latest_adoption':unit.adoption,
                'context':merged_context.model_dump(mode='json'),
                'execution_segments':execution_segments(merged,merged_snapshot.execution),
                'execution_surplus':execution_surplus(merged,merged_snapshot.execution),
                'reset_preview':replayed.lineage.get('reset_preview'),
                'reset_participants':self._reset_participants(unit,merged_snapshot,unit.context.lineage|replayed.lineage),
                'reset_reference':replayed.lineage.get('reset_reference'),
                'context_changes':differences(unit.context.model_dump(mode='json'),merged_context.model_dump(mode='json')),
                'plan':merged.model_dump(mode='json'),'snapshot':merged_snapshot.model_dump(mode='json'),'hour_display':plan_hour_display(merged),
                'evaluation':result,'effects':[list(k) for k in sorted(effects)],
                'changes':plan_differences(unit.plan.model_dump(mode='json'),merged.model_dump(mode='json')),
                'unallocated':replayed.unallocated, 'operation_issues':replayed.issues,
                'adoption_blocked':any(issue.get('blocks_adoption',issue.get('severity')=='error') for issue in replayed.issues), 'merged':command.base_revision!=unit.revision,
                'coverage':[r.model_dump(mode='json') for r in unit.context.coverage],
                'parent_revision':unit.revision, 'base_revision':command.base_revision,
                'lineage':unit.get_planning_revision(unit.revision).get('lineage',{})|unit.context.lineage|replayed.lineage}

    def preview(self, command, actor, now):
        return self.store.read(actor,lambda unit:self._prepare(unit,command,now,actor))

    def _adopted_coverage(self,unit,prepared):
        from .production_time import production_week
        old=unit.get_planning_adoption(unit.adoption)['coverage'] if unit.adoption else []
        coverage=[ExecutionCoverage.model_validate(row) for row in old+prepared['coverage']+prepared['lineage'].get('planned_coverage',[])]
        plan=Plan.model_validate(prepared['plan'])
        time=EvaluationTime.model_validate(prepared['snapshot']['time'])
        facts=prepared['snapshot']['execution']['facts']
        lines=sorted({r.code for r in unit.inputs.masters.lines if r.active}|{r.line_code for r in plan.segments}|{r['line_code'] for r in facts})
        models=sorted({r.code for r in unit.inputs.masters.models if r.active}|{r.model_code for r in plan.segments}|{r['model_code'] for r in facts})
        # Imported partial coverage stays partial. Only a newly planned week
        # without an imported boundary gains explicit adopted blank-zero cells.
        for week in sorted({production_week(r.date) for r in plan.segments if r.quantity and time.window_start<=r.date<=time.window_end}):
            end=week+timedelta(days=6)
            if any(r.start_date<=end and r.end_date>=week for r in coverage):
                continue
            coverage.append(ExecutionCoverage(start_date=week,end_date=end,line_codes=lines,model_codes=models,blanks_are_zero=True))
        unique={r.model_dump_json():r.model_dump(mode='json') for r in coverage}
        return list(unique.values())

    def submit(self, command, actor, now, kind='save'):
        def action(unit):
            if kind=='adopt' and command.latest_adoption!=unit.adoption:
                raise PlanProblem(409,'采用版已变化，请重新核对',latest_adoption=unit.adoption,revision=unit.revision)
            prepared=self._prepare(unit,command,now,actor)
            reference=prepared.get('reset_reference')
            if reference and command.operations and all(r.get('kind')=='weekly_reset' for r in command.operations):
                prepared['lineage'].pop('reset_reference',None)
                saved=unit.append(prepared,command.operations,actor,now,'reset_reference',unit.get_planning_revision(unit.revision).get('pending',False))
                return saved|{'already_effective':True,**reference}
            if kind=='adopt' and (not prepared['evaluation']['executable'] or prepared['adoption_blocked']):
                raise PlanProblem(422,'存在硬冲突、未完成计算或必要确认，不能采用',evaluation=prepared['evaluation'])
            if kind=='adopt':
                prepared['coverage']=self._adopted_coverage(unit,prepared)
            if kind=='save' and not command.pending and (not prepared['evaluation']['executable'] or prepared['adoption_blocked']):
                raise PlanProblem(422,'请明确保存为待处理草稿',evaluation=prepared['evaluation'])
            if kind=='adopt' and prepared.get('reset_preview'):
                context=PlanningContext.model_validate(prepared['context'])
                records=context.lineage.get('resets',{})
                key=prepared['snapshot']['time']['window_start']
                record=records.get(key)
                if record and record.get('draft_action'):
                    if not record.get('effective_adoption') and record['source_adoption']!=unit.adoption:
                        raise PlanProblem(409,'首次重置来源采用版已变化，请重新核对来源与快照',source_adoption=unit.adoption)
                    record=record|{'effective_adoption':unit.adoption+1,'effective_revision':unit.revision+1,
                        'first_effective_revision':record.get('first_effective_revision') or unit.revision+1,'draft_action':None}
                    lineage=context.lineage|{'resets':records|{key:record},'reset_preview':record}
                    context=context.model_copy(update={'lineage':lineage})
                    prepared['context']=context.model_dump(mode='json')
                    prepared['context_changes']=differences(unit.context.model_dump(mode='json'),prepared['context'])
                    prepared['lineage'].update(lineage);prepared['reset_preview']=record
            if prepared['context_changes']:
                unit.write_context(PlanningContext.model_validate(prepared['context']),actor,now)
                prepared['input_version']=unit.inputs.input_version
                prepared['snapshot']['inputs']['input_version']=unit.inputs.input_version
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
            record=unit.get_planning_revision(unit.revision)
            from .reset_operations import refresh_lineage
            lineage=refresh_lineage(record.get('lineage',{})|unit.context.lineage,unit.plan,snapshot)
            return {'revision':unit.revision,'latest_adoption':unit.adoption,'input_version':unit.inputs.input_version,
                    'pending':record.get('pending',False), 'kind':record.get('kind','empty'),
                    'unallocated':record.get('unallocated',[]), 'operation_issues':record.get('operation_issues',[]),
                    'lineage':lineage, 'coverage':record.get('coverage',[]),
                    'plan':unit.plan.model_dump(mode='json'),'context':unit.context.model_copy(update={'execution':snapshot.execution}).model_dump(mode='json'),
                    'execution_segments':execution_segments(unit.plan,snapshot.execution),
                    'execution_surplus':execution_surplus(unit.plan,snapshot.execution),
                    'reset_preview':lineage.get('reset_preview'),
                    'reset_participants':self._reset_participants(unit,snapshot,lineage),
                    'time':snapshot.time.model_dump(mode='json'),'hour_display':plan_hour_display(unit.plan),
                    'weeks': [{'number':week+1,'label':week_label(snapshot.time.window_start+timedelta(days=week*7)),'start_date':(snapshot.time.window_start+timedelta(days=week*7)).isoformat(),
                        'dates':[(snapshot.time.window_start+timedelta(days=week*7+day)).isoformat() for day in range(7)]} for week in range(15)],
                    'evaluation':self._evaluation_json(snapshot)}
        return self.store.read(actor,current)

    def compare(self, base_revision, input_version, actor):
        def compare(unit):
            base=unit.get_planning_revision(base_revision)
            old_inputs=unit.get_inputs(input_version)
            old_context=unit.get_context(input_version)
            return {'base_revision':base_revision,'base_input_version':input_version,
                    'revision':unit.revision,'input_version':unit.inputs.input_version,'latest_adoption':unit.adoption,
                    'plan_changes':plan_differences(base['snapshot']['plan'],unit.plan.model_dump(mode='json')),
                    'input_changes':differences(old_inputs.model_dump(mode='json'),unit.inputs.model_dump(mode='json')) +
                        differences(old_context.model_dump(mode='json'),unit.context.model_dump(mode='json'),('context',))}
        return self.store.read(actor,compare)

    def revision(self, identity, actor):
        return self.store.read(actor,lambda unit:unit.get_revision(identity))

    def history(self, actor, now, before=None, limit=50):
        from .history import RECOVERY_DAYS
        return self.store.read(actor,lambda unit:unit.list_history(now-timedelta(days=RECOVERY_DAYS),before,limit))

    def adoption(self, identity, actor):
        return self.store.read(actor,lambda unit:unit.get_adoption(identity))

    def verify(self, operation_id, actor, now):
        return self.store.verify(str(operation_id),actor,now)
