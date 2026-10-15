"""Explicit test adapter for the persistence boundary; no concurrency claims."""
from copy import deepcopy
import hashlib
import json
from threading import RLock
from auto_plan.plans import PlanningContext, PlanProblem
from auto_plan.plan_models import Plan


def zero_revision():
    return {'snapshot':{'plan':Plan().model_dump(mode='json')},'coverage':[],'lineage':{},'operations':[],'effects':[]}


class MemoryPlanStore:
    def __init__(self,inputs):
        self.input_store=inputs; self.lock=RLock(); self.revision=0; self.adoption=0
        self.cursor=self
        self.plan=Plan(); self.context=PlanningContext(); self.contexts={self.inputs.input_version:self.context.model_copy(deep=True)}; self.revisions={0:zero_revision()}; self.adoptions={}; self.keys={}
    @property
    def inputs(self): return self.input_store.read()
    def get_inputs(self,version): return self.input_store.read(version)
    def get_context(self,version):
        if version==self.inputs.input_version: return self.context.model_copy(deep=True)
        eligible=[number for number in self.contexts if number<=version]
        return self.contexts[max(eligible)].model_copy(deep=True) if eligible else PlanningContext()
    def get_revision(self,identity): return deepcopy(self.revisions[identity])
    def get_adoption(self,identity): return deepcopy(self.adoptions[identity])
    def effects_since(self,revision): return {tuple(k) for number,row in self.revisions.items() if number>revision for k in row['effects']}
    def read(self,actor,action):
        with self.lock: return action(self)
    def submit(self,command,actor,now,kind,action):
        with self.lock:
            key=str(command.operation_id)
            digest=hashlib.sha256(json.dumps(command.model_dump(mode='json'),ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            if key in self.keys:
                record=self.keys[key]
                if record['actor']!=actor or (record['result']['status']=='COMMITTED' and (record['kind']!=kind or record['digest']!=digest)): raise PlanProblem(409,'操作编号已使用')
                if record['result']['status']=='ABORTED_VOID': raise PlanProblem(409,'前次操作已作废，请由用户重新发起',result=record['result'])
                return deepcopy(record['result'])
            result=action(self)
            self.keys[key]={'actor':actor,'kind':kind,'digest':digest,'result':deepcopy(result)}
            return result
    def write_context(self,context,actor,now):
        self.contexts[self.inputs.input_version]=self.context.model_copy(deep=True)
        self.input_store.update(actor,now,lambda current:current.model_copy(update={'input_version':current.input_version+1}))
        self.context=context
        self.contexts[self.inputs.input_version]=context.model_copy(deep=True)
    def append(self,prepared,operations,actor,now,kind,pending):
        self.revision+=1
        prepared=deepcopy(prepared)|{'revision':self.revision,'operations':deepcopy(operations),'actor':actor,'recorded_at':now.isoformat(),'pending':pending,'kind':kind}
        if kind=='adopt':
            self.adoption+=1; prepared['latest_adoption']=self.adoption; self.adoptions[self.adoption]=deepcopy(prepared)
        self.revisions[self.revision]=deepcopy(prepared); self.plan=Plan.model_validate(prepared['plan'])
        return {'status':'COMMITTED','revision':self.revision,'latest_adoption':self.adoption,'input_version':self.inputs.input_version,'pending':pending}
    def verify(self,key,actor,now):
        with self.lock:
            if key not in self.keys: self.keys[key]={'actor':actor,'result':{'status':'ABORTED_VOID','operation_id':key}}
            if self.keys[key]['actor']!=actor: raise PlanProblem(409,'操作编号已使用')
            return deepcopy(self.keys[key]['result'])

    def initialize_in_transaction(self,cursor,actor,now,inputs,context,plan,lineage,operation_id):
        from auto_plan.plans import PlanService, PlanCommand
        self.contexts[self.inputs.input_version]=self.context.model_copy(deep=True)
        saved=self.input_store.update(actor,now,lambda current:inputs.model_copy(update={'input_version':current.input_version+1}))
        self.context=context.model_copy(update={'lineage':lineage})
        self.contexts[saved.input_version]=self.context.model_copy(deep=True)
        prepared=PlanService(self)._prepare(self,PlanCommand(base_revision=0,input_version=saved.input_version,latest_adoption=0,operations=[{'kind':'replace_plan','plan':plan.model_dump(mode='json')}]),now)
        return self.append(prepared,[],actor,now,'initialize',True)
