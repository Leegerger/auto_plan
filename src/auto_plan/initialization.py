"""Validate standard first-import data without changing live input or plan state."""
from datetime import timedelta
from typing import Any
from pydantic import Field, ValidationError
from .input_models import InputRow, Pieces, WeeklyDemand
from .inputs import preview as preview_inputs, error
from .plan_models import SourceSegment, InitialRecipeState, ExecutionCoverage, ExecutionContext, ResetContext, EvaluationSnapshot, EvaluationTime
from .plans import PlanningContext, PlanProblem
from .production_time import BEIJING, production_cutoff
from .certificate import construct_hour_certificate, CertificateError
from .rules import evaluate


class InitialImport(InputRow):
    base_revision: Pieces
    input_version: Pieces
    latest_adoption: Pieces
    changes: dict[str,list[dict[str,Any]]]
    segments: list[dict[str,Any]]
    demands: list[dict[str,Any]]
    complete_weekly_demands: bool = Field(strict=True)
    coverage: list[dict[str,Any]]
    reset: dict[str,Any]
    execution: dict[str,Any]
    initial_states: list[dict[str,Any]] = Field(default_factory=list)


def week_label(day):
    year,week,_=day.isocalendar()
    return f'W{year:04d}{week:02d}'


def typed_rows(model, rows, sheet, errors):
    result=[]
    for index,raw in enumerate(rows,2):
        location=raw.get('_row',index)
        try:
            result.append(model.model_validate({k:v for k,v in raw.items() if k not in {'_row','week_label'}}))
            if 'week_label' in raw and raw['week_label']!=week_label(result[-1].week):
                errors.append(error(sheet,location,'week_label','周别与周一日期不一致'))
        except ValidationError as exc:
            for detail in exc.errors():
                errors.append(error(sheet,location,'.'.join(map(str,detail['loc'])),str(detail.get('ctx',{}).get('error','字段必填或格式无效'))))
    return result


def prepare_initial(current, head, payload, now):
    if head['revision'] or head['latest_adoption'] or payload.base_revision or payload.latest_adoption:
        raise PlanProblem(409,'已有共享计划，不能重复首次接入')
    maintained=preview_inputs(current,payload.input_version,payload.changes)
    errors=list(maintained['errors']); time=EvaluationTime.at(now)
    segments=typed_rows(SourceSegment,payload.segments,'segments',errors)
    demands=typed_rows(WeeklyDemand,payload.demands,'demands',errors)
    coverage=typed_rows(ExecutionCoverage,payload.coverage,'coverage',errors)
    states=typed_rows(InitialRecipeState,payload.initial_states,'initial_states',errors)
    reset=typed_rows(ResetContext,[payload.reset],'reset',errors)
    execution=typed_rows(ExecutionContext,[payload.execution],'execution',errors)
    if not payload.complete_weekly_demands:
        errors.append(error('demands',1,'complete_weekly_demands','须确认填写完整整周需求，包含已经满足部分'))
    if not coverage: errors.append(error('coverage',2,'start_date','必须明确旧计划覆盖范围和空白零值口径'))
    if execution and (not execution[0].coverage or execution[0].cutoff is None):
        errors.append(error('execution',2,'coverage','必须明确已确认执行事实范围和截止时点'))
    for sheet,rows in [('segments',segments),('demands',demands),('coverage',coverage)]:
        for i,row in enumerate(rows,2):
            start=getattr(row,'date',getattr(row,'week',getattr(row,'start_date',None)))
            end=getattr(row,'end_date',start)
            if start and not (time.window_start<=start<=end<=time.window_end):
                errors.append(error(sheet,i,'date','首次承接仅限当前十五周窗口'))
    if reset and (reset[0].production_week!=time.window_start or reset[0].cutoff!=production_cutoff(time.window_start)):
        errors.append(error('reset',2,'cutoff','周一快照必须属于当前生产周周一08:00北京时间'))
    participating={r.model_code for r in segments+demands}
    for row in coverage: participating.update(row.model_codes)
    if execution:
        participating.update(r.model_code for r in execution[0].facts)
        for row in execution[0].coverage: participating.update(row.model_codes)
    expected={(model,time.window_start+timedelta(weeks=i)) for model in participating for i in range(15)}
    actual={(r.model_code,r.week) for r in demands}
    if len(actual)!=len(demands): errors.append(error('demands',2,'model_code,week','完整周需求识别组合重复'))
    for model,day in sorted(expected-actual):
        errors.append(error('demands',2,'quantity',f'{model} {day} 须显式填写整周需求，零也须填写'))
    if reset:
        codes=[r.model_code for r in reset[0].models]
        if len(codes)!=len(set(codes)): errors.append(error('reset',2,'models','周一快照机种重复'))
        for model in sorted(participating-set(codes)): errors.append(error('reset',2,'models',f'{model} 须成对填写在制品和净余额，零也须填写'))
    from .input_models import InputSnapshot
    inputs=InputSnapshot.model_validate(maintained['snapshot']) if maintained['snapshot'] else current
    model_index={r.code:r for r in inputs.masters.models}; lines={r.code:r for r in inputs.masters.lines}
    for sheet,rows in [('segments',segments),('demands',demands),('reset',reset[0].models if reset else [])]:
        for i,row in enumerate(rows,2):
            if row.model_code not in model_index: errors.append(error(sheet,i,'model_code','机种编码不存在'))
            elif not model_index[row.model_code].active: errors.append(error(sheet,i,'model_code','已停用的机种不能新增引用'))
            if hasattr(row,'line_code') and row.line_code not in lines: errors.append(error(sheet,i,'line_code','线体编码不存在'))
    for sheet,rows in [('coverage',coverage),('execution',execution[0].coverage if execution else [])]:
        for i,row in enumerate(rows,2):
            if not row.model_codes or not row.line_codes: errors.append(error(sheet,i,'coverage','覆盖范围须包含明确机种和线体'))
            for code in row.model_codes:
                if code not in model_index: errors.append(error(sheet,i,'model_codes','机种编码不存在'))
            for code in row.line_codes:
                if code not in lines: errors.append(error(sheet,i,'line_codes','线体编码不存在'))
    if execution:
        if execution[0].cutoff and execution[0].cutoff>now: errors.append(error('execution',2,'cutoff','执行事实截止时点不能晚于当前时点'))
        fact_keys=set()
        for i,row in enumerate(execution[0].facts,2):
            key=(row.date,row.line_code,row.model_code)
            if key in fact_keys: errors.append(error('execution',i,'facts','同日线体机种的累计执行事实重复'))
            fact_keys.add(key)
            if row.model_code not in model_index or row.line_code not in lines: errors.append(error('execution',i,'facts','执行事实编码不存在'))
            if not time.window_start<=row.date<=time.window_end: errors.append(error('execution',i,'date','首次执行事实仅承接当前窗口'))
    for i,row in enumerate(segments,2):
        if not any(c.start_date<=row.date<=c.end_date and row.line_code in c.line_codes and row.model_code in c.model_codes for c in coverage):
            errors.append(error('segments',i,'coverage','导入的旧计划生产段必须位于明确声明的旧计划覆盖范围内'))
    inputs=inputs.model_copy(update={'business':inputs.business.model_copy(update={'demands':demands})})
    lineage={'enablement_date':now.astimezone(BEIJING).date().isoformat(),
             'external_reset':{'production_week':time.window_start.isoformat(),'cutoff':production_cutoff(time.window_start).isoformat(),'batch_id':reset[0].batch_id if reset else '', 'already_applied':True,'system_correction_available':False},
             'initial_coverage':[r.model_dump(mode='json') for r in coverage]}
    result={'valid':not errors,'errors':errors,'inputs':inputs.model_dump(mode='json'),'lineage':lineage,
            'base_revision':head['revision'],'input_version':current.input_version,'latest_adoption':head['latest_adoption']}
    if errors: return result
    try: plan=construct_hour_certificate(inputs,segments,states,window_start=time.window_start)
    except CertificateError as exc:
        result.update(valid=False,errors=[error('segments',next((r.get('_row',i) for i,r in enumerate(payload.segments,2) if r.get('id')==issue.segment_id),2),'quantity',issue.reason) for issue in exc.issues]); return result
    context=PlanningContext(execution=execution[0],reset=reset[0],coverage=coverage,lineage=lineage)
    evaluated=evaluate(EvaluationSnapshot(inputs=inputs,time=time,plan=plan,execution=context.execution,reset=context.reset))
    result.update(plan=plan.model_dump(mode='json'),context=context.model_dump(mode='json'),evaluation=evaluated.model_dump(mode='json'))
    if not evaluated.capacity_valid or not evaluated.computation_complete:
        result.update(valid=False,errors=[error('evaluation',2,issue.code,issue.reason) for issue in evaluated.issues if issue.severity in {'error','incomplete'}])
    return result
