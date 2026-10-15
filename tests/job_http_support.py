"""Read a durable calculation through its public acknowledgements and references."""
import time
from uuid import uuid4
import httpx


def calculate(client,payload,timeout=150):
    accepted=client.post('/auto_plan/api/jobs',json={'submission_id':str(uuid4()),**payload})
    if accepted.status_code!=200: return accepted
    identity=accepted.json()['job_id']
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        response=client.get('/auto_plan/api/jobs/'+identity)
        if response.status_code!=200: return response
        job=response.json()
        if job['state'] not in {'queued','running'}:
            if job['state'] not in {'completed','stopped'}:
                return httpx.Response(500,json={'detail':'Durable calculation failed','job':job})
            candidates=[]
            for reference in job['candidates']:
                viewed=client.get(f"/auto_plan/api/jobs/{identity}/candidates/{reference['candidate_id']}")
                if viewed.status_code!=200: return viewed
                value=viewed.json()
                candidates.append(value['candidate']|{'server_reference':value['proposal']})
            return httpx.Response(200,json={**job['result'],'candidates':candidates,'job_id':identity})
        time.sleep(.02)
    raise AssertionError('Durable calculation exceeded test deadline')
