"""Deterministic job persistence adapter. Real arbitration is tested in Oracle."""
from copy import deepcopy
from uuid import uuid4
from threading import RLock
from auto_plan.access import AuthorizationDenied
from auto_plan.plans import PlanProblem


class MemoryJobStore:
    def __init__(self, plans, access, queue_capacity=20, worker_limit=1):
        self.plans, self.access = plans, access
        self.jobs, self.keys, self.candidates = {}, {}, {}
        self.queue_capacity, self.worker_limit = queue_capacity, worker_limit
        self.lock = RLock()
        self.generation = None

    def authorize(self, actor, editor=False):
        row = self.access.users.get(actor, {})
        if not row.get('active') or not (set(row.get('roles', [])) & ({'editor'} if editor else {'viewer','editor'})):
            raise AuthorizationDenied

    def submit(self, command, actor, now, freeze):
        with self.lock:
            self.authorize(actor, True)
            key = str(command.submission_id)
            body = command.model_dump(mode='json')
            if key in self.keys:
                who, original, result = self.keys[key]
                if who != actor or original != body or result['status'] == 'ABORTED_VOID':
                    raise PlanProblem(409, '提交编号已使用或作废')
                return deepcopy(result)
            if sum(j['state']=='queued' for j in self.jobs.values()) >= self.queue_capacity:
                raise PlanProblem(429,'任务队列已满')
            if any(j['actor']==actor and j['state']=='queued' for j in self.jobs.values()):
                raise PlanProblem(429,'每个账号最多一个运行加一个排队任务')
            frozen = freeze(self.plans)
            identity = str(uuid4())
            result = {'status':'ACCEPTED','submission_id':key,'job_id':identity}
            self.keys[key] = actor, body, result
            self.jobs[identity] = {'job_id':identity,'actor':actor,'state':'queued','fifo':len(self.jobs)+1,
                'source_revision':frozen.source_revision,'input_version':frozen.input_version,'latest_adoption':frozen.latest_adoption,
                'frozen':frozen,'candidates':[],'created_at':now.isoformat(),'stop_requested_at':None}
            return deepcopy(result)

    def verify(self,key,actor,now):
        with self.lock:
            self.authorize(actor,True)
            if key not in self.keys: self.keys[key] = actor,None,{'status':'ABORTED_VOID','submission_id':key}
            if self.keys[key][0] != actor: raise PlanProblem(409,'提交编号已使用')
            return deepcopy(self.keys[key][2])

    def list(self,actor,now):
        self.authorize(actor)
        return {'jobs':[self.get(k,actor,now) for k in reversed(self.jobs)],'queue_capacity':self.queue_capacity,'worker_limit':self.worker_limit}

    def get(self,identity,actor,now):
        self.authorize(actor)
        if identity not in self.jobs: raise KeyError(identity)
        return deepcopy({k:v for k,v in self.jobs[identity].items() if k!='frozen'})

    def recover_startup(self,now):
        self.generation=str(uuid4())
        for job in self.jobs.values():
            if job['state']=='running': job['state']='interrupted'
        return self.generation

    def check_consistency(self): return True
    def cleanup(self,now): return 0

    def claim(self,now):
        with self.lock:
            running=[j for j in self.jobs.values() if j['state']=='running']
            if len(running)>=self.worker_limit: return None
            queued=[j for j in self.jobs.values() if j['state']=='queued']
            if not queued: return None
            job=queued[0]
            if any(j['actor']==job['actor'] for j in running): return None
            self.authorize(job['actor'],True)
            job.update(state='running',run_id=str(uuid4()),generation=self.generation)
            return {k:job[k] for k in ['job_id','run_id','generation','frozen']}

    def control(self,identity,run,generation,now):
        with self.lock:
            job=self.jobs[identity]
            if job['state']!='running' or job['run_id']!=run or generation!=self.generation: return {'owned':False}
            return {'owned':True,'stop_requested_at':job['stop_requested_at']}

    def stop(self,identity,actor,now):
        with self.lock:
            job=self.jobs[identity]
            roles=self.access.users.get(actor,{}).get('roles',[])
            if actor!=job['actor'] and 'administrator' not in roles: raise AuthorizationDenied
            if job['state']=='queued': job['state']='stopped'
            if job['state']=='running': job['stop_requested_at']=now
            return {'job_id':identity,'state':job['state'],'stop_requested_at':now.isoformat()}

    def request_shutdown(self,identity,run,generation,now):
        self.jobs[identity]['stop_requested_at']=now

    def publish(self,identity,run,generation,candidate,now):
        from auto_plan.job_codec import digest
        with self.lock:
            job=self.jobs[identity]
            if job['state']!='running' or job['run_id']!=run or generation!=self.generation: raise PlanProblem(409,'任务已中断')
            sha=digest(candidate.model_dump(mode='json'))
            prior=next((key for key,value in self.candidates.items() if value[0]==identity and value[2]==sha),None)
            cid=prior or str(uuid4())
            self.candidates[cid]=(identity,candidate.model_copy(deep=True),sha,now)
            job['candidates']=[{'candidate_id':cid,'payload_sha':sha,'expired':False}]
            self.plans.get_candidate=self.get_candidate
            return {'candidate_id':cid,'payload_sha':sha}

    def get_candidate(self,identity,cid,sha,now):
        from datetime import timedelta
        row=self.candidates[cid]
        if row[0]!=identity or row[2]!=sha: raise PlanProblem(409,'引用不一致')
        if row[3]+timedelta(days=30)<=now: raise PlanProblem(410,'候选过期')
        return row[1].model_copy(deep=True)

    def candidate(self,identity,cid,actor,now):
        self.authorize(actor)
        row=self.candidates[cid]
        content=self.get_candidate(identity,cid,row[2],now)
        self.candidates[cid]=(*row[:3],now)
        return {'job_id':identity,'candidate_id':cid,'payload_sha':row[2],'candidate':content.model_dump(mode='json'),
            'proposal':{'base_revision':content.source_revision,'input_version':content.input_version,'latest_adoption':content.latest_adoption,
                'operations':[{'kind':'apply_candidate','job_id':identity,'candidate_id':cid,'payload_sha':row[2]}]}}

    def finish(self,identity,run,generation,state,result,manifest,now):
        with self.lock:
            job=self.jobs[identity]
            if job['state']!='running' or job['run_id']!=run or generation!=self.generation: raise PlanProblem(409,'任务已中断')
            job.update(state=state,result=deepcopy(result))
            if manifest:
                job['candidates']=[{'candidate_id':cid,'payload_sha':value[2],'expired':False} for cid,value in self.candidates.items() if value[0]==identity and value[2] in manifest]

    def rerun(self,identity,command,actor,now):
        from auto_plan.jobs import SubmitJob
        frozen=self.jobs[identity]['frozen']
        body=SubmitJob(submission_id=command.submission_id,base_revision=frozen.source_revision,input_version=frozen.input_version,latest_adoption=frozen.latest_adoption,request=frozen.request)
        return self.submit(body,actor,now,lambda unit:frozen)

    def progress(self,identity,run,generation,event,now):
        self.jobs[identity]['progress']=deepcopy(event)
