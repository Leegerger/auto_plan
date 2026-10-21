"""Explicit memory persistence adapter, with no Oracle concurrency claims."""
from copy import deepcopy
import hashlib
from auto_plan.oracle_plans import document
from auto_plan.plans import PlanProblem
from auto_plan.input_models import WeeklyDemand

class MemoryDemandStore:
    def __init__(self,plans): self.plans=plans; self.records=[]; self.keys={}
    def read(self,actor,action): return self.plans.read(actor,lambda unit:action(unit,deepcopy(self.records)))
    def insert_file(self,cursor,record,version): self.records.append(deepcopy(record)|{'input_version':version})
    def append(self,unit,record,demands,actor,now):
        saved=unit.input_store.update(actor,now,lambda current:current.model_copy(update={'input_version':current.input_version+1,'business':current.business.model_copy(update={'demands':[WeeklyDemand.model_validate(r) for r in demands]})}))
        self.insert_file(unit,record,saved.input_version)
        return {'status':'COMMITTED','input_version':saved.input_version,'file_id':record['file_id'],'attribution_date':record['attribution_date'],'version':record['version']}
    def submit(self,payload,actor,now,action):
        with self.plans.lock:
            key=str(payload.operation_id); sha=hashlib.sha256(document(payload.model_dump(mode='json'))).hexdigest()
            if key in self.keys:
                stored=self.keys[key]
                if stored['actor']!=actor or stored.get('sha')!=sha: raise PlanProblem(409,'需求操作编号已使用')
                return deepcopy(stored['result'])
            result=action(self.plans,deepcopy(self.records)); self.keys[key]={'actor':actor,'sha':sha,'result':deepcopy(result)}
            return result
    def verify(self,key,actor,now):
        with self.plans.lock:
            if key not in self.keys: self.keys[key]={'actor':actor,'result':{'status':'ABORTED_VOID','operation_id':key}}
            if self.keys[key]['actor']!=actor: raise PlanProblem(409,'需求操作编号已使用')
            return deepcopy(self.keys[key]['result'])
