"""Whole-file demand replacement and ordered, nonnegative replay."""
import re
from datetime import date
from typing import Annotated
from uuid import UUID, uuid5
from pydantic import BeforeValidator, Field, ValidationError, field_validator
from .input_models import InputRow, Code, Pieces, ProductionDate, WeeklyDemand, BusinessInputs
from .production_time import BEIJING
from .initialization import week_label
from .inputs import error
from .plans import PlanProblem


def signed_integer(value):
    if isinstance(value,str) and re.fullmatch(r'[+-]?\d+',value.strip()): value=int(value.strip())
    if type(value) is not int: raise ValueError('必须填写有符号整数，不能使用小数或布尔值')
    return value


SignedPieces=Annotated[int,BeforeValidator(signed_integer)]


class DemandRow(InputRow):
    model_code: Code
    week: ProductionDate
    delta: SignedPieces
    week_label: str | None = None
    source_row: Pieces = 2
    @field_validator('week')
    @classmethod
    def monday(cls,value): return WeeklyDemand.monday(value)
    @field_validator('week_label')
    @classmethod
    def matching_week(cls,value,info):
        if value and 'week' in info.data and value!=week_label(info.data['week']): raise ValueError('周别与周一日期不一致')
        return value


class DemandCommand(InputRow):
    input_version: Pieces
    attribution_date: ProductionDate | None = None
    rows: list[dict] = Field(max_length=100000)


class SubmitDemand(DemandCommand):
    operation_id: UUID


def file_record(rows,day,key,actor,now,version):
    identity=uuid5(key,'demand-file')
    return {'file_id':str(identity),'attribution_date':day.isoformat(),'version':version,
            'actor':actor,'recorded_at':now.isoformat(),
            'rows':[row.model_dump(mode='json')|{'row_id':str(uuid5(identity,str(index))),'ordinal':index} for index,row in enumerate(rows)]}


def preview_replacement(unit,payload,files,now):
    if payload.input_version!=unit.inputs.input_version: raise PlanProblem(409,'输入资料已变化，请重新读取并预览',input_version=unit.inputs.input_version)
    enabled=unit.context.lineage.get('enablement_date')
    if not enabled: raise PlanProblem(409,'请先完成首次接入')
    day=payload.attribution_date or now.astimezone(BEIJING).date()
    errors=[]; rows=[]
    if not date.fromisoformat(enabled)<=day<=now.astimezone(BEIJING).date(): errors.append(error('demand_changes',1,'attribution_date','变更归属日期须在启用自然日至今天之间，不补录启用前历史'))
    models={row.code:row for row in unit.inputs.masters.models}
    for i,raw in enumerate(payload.rows,2):
        source_row=raw.get('_row',raw.get('source_row',i))
        try: row=DemandRow.model_validate({k:v for k,v in raw.items() if k!='_row'}|{'source_row':source_row})
        except ValidationError as exc:
            for detail in exc.errors(): errors.append(error('demand_changes',source_row,'.'.join(map(str,detail['loc'])),str(detail.get('ctx',{}).get('error','字段必填或格式无效'))))
            continue
        if row.model_code not in models: errors.append(error('demand_changes',source_row,'model_code','机种编码不存在'))
        elif not models[row.model_code].active: errors.append(error('demand_changes',source_row,'model_code','已停用的机种不能新增需求引用'))
        rows.append(row)
    effective={}
    for record in files:
        previous=effective.get(record['attribution_date'])
        if previous is None or record['version']>previous['version']: effective[record['attribution_date']]=record
    replacement={'attribution_date':day.isoformat(),'rows':[r.model_dump(mode='json') for r in rows]}
    effective[day.isoformat()]=replacement
    totals={}
    for record in sorted(effective.values(),key=lambda r:r['attribution_date']):
        for row in record['rows']:
            identity=(row['model_code'],row['week']); total=totals.get(identity,0)+row['delta']
            if total<0: errors.append(error('demand_changes',row['source_row'],'delta',f"{record['attribution_date']} {row['model_code']} {row['week']} 累计中途为负数 {total}"))
            totals[identity]=total
    demands=[WeeklyDemand(model_code=model,week=week,quantity=quantity) for (model,week),quantity in sorted(totals.items()) if quantity>=0]
    result={'valid':not errors,'errors':errors,'input_version':unit.inputs.input_version,'attribution_date':day.isoformat(),
            'rows':[r.model_dump(mode='json') for r in rows],'demands':[r.model_dump(mode='json') for r in demands]}
    if not errors:
        from .plan_models import EvaluationSnapshot, EvaluationTime
        from .rules import evaluate
        inputs=unit.inputs.model_copy(update={'business':unit.inputs.business.model_copy(update={'demands':demands})})
        snapshot=EvaluationSnapshot(inputs=inputs,time=EvaluationTime.at(now),plan=unit.plan,
            execution=unit.context.execution,reset=unit.context.reset,locks=unit.context.locks,preferences=unit.context.preferences)
        result['plan']=unit.plan.model_dump(mode='json')
        result['evaluation']=evaluate(snapshot).model_dump(mode='json')
    return result


class DemandService:
    def __init__(self,store): self.store=store
    def preview(self,payload,actor,now): return self.store.read(actor,lambda unit,files:preview_replacement(unit,payload,files,now))
    def apply(self,payload,actor,now):
        def action(unit,files):
            result=preview_replacement(unit,payload,files,now)
            if not result['valid']: raise PlanProblem(422,'整批未提交，请修正需求变更',errors=result['errors'])
            record=file_record([DemandRow.model_validate(r) for r in result['rows']],date.fromisoformat(result['attribution_date']),payload.operation_id,actor,now,1+max((f['version'] for f in files if f['attribution_date']==result['attribution_date']),default=0))
            return self.store.append(unit,record,result['demands'],actor,now)
        return self.store.submit(payload,actor,now,action)
    def files(self,actor):
        def read(unit,files):
            latest={}
            for record in files: latest[record['attribution_date']]=max(latest.get(record['attribution_date'],0),record['version'])
            return {'input_version':unit.inputs.input_version,'enablement_date':unit.context.lineage.get('enablement_date'),'files':[r|{'effective':r['version']==latest[r['attribution_date']]} for r in files]}
        return self.store.read(actor,read)
