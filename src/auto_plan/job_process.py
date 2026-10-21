"""Bounded process supervision with database-independent physical stop timers."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import ctypes
import json
import os
from pathlib import Path
from queue import Queue, Empty
import signal
import struct
import subprocess
import sys
import tempfile
from threading import Event, Lock, Thread
import time
from .access import StoreUnavailable
from .jobs import JobUnknown
from .plans import PlanProblem, PlanBusy
from .job_codec import document, PAYLOAD_LIMIT, METADATA_LIMIT
from .solver_process import compute_environment


def allocated_cpus():
    try:
        available=len(os.sched_getaffinity(0))
    except (AttributeError,OSError):
        available=os.cpu_count() or 1
    try:
        quota,period=Path('/sys/fs/cgroup/cpu.max').read_text().split()
        if quota!='max': available=min(available,max(1,int(quota)//int(period)))
    except (OSError,ValueError):
        # Some container managers expose the cgroup-v1 quota files instead.
        try:
            quota=int(Path('/sys/fs/cgroup/cpu/cpu.cfs_quota_us').read_text())
            period=int(Path('/sys/fs/cgroup/cpu/cpu.cfs_period_us').read_text())
            if quota>0: available=min(available,max(1,quota//period))
        except (OSError,ValueError): pass
    return max(1,available)


def worker_allocation(requested=None):
    ceiling=max(0,allocated_cpus()-1)
    return ceiling if requested is None else min(requested,ceiling)


class ComputeProcess:
    command = [sys.executable,'-m','auto_plan.job_compute']

    def __init__(self,frozen,threads=1):
        self.directory=tempfile.TemporaryDirectory(prefix='auto-plan-job-')
        self.cancel=Path(self.directory.name)/'stop'
        self.events=Queue(maxsize=2)
        self.closed=Event()
        self.errors=bytearray()
        self.input=tempfile.TemporaryFile()
        self.input.write(document({'frozen':frozen.model_dump(mode='json'),'cancel_file':str(self.cancel)}))
        self.input.seek(0)
        self.child=subprocess.Popen(self.command,stdin=self.input,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=compute_environment(threads),
            start_new_session=True,close_fds=True)
        self.reader=Thread(target=self._read,daemon=True);self.reader.start()
        self.stderr_reader=Thread(target=self._stderr,daemon=True);self.stderr_reader.start()

    def _offer(self,value):
        while not self.closed.is_set():
            try: self.events.put(value,timeout=.1);return
            except __import__('queue').Full: pass

    def _read(self):
        try:
            while not self.closed.is_set():
                header=self.child.stdout.read(4)
                if not header: break
                if len(header)!=4: raise ValueError('Truncated frame header')
                length=struct.unpack('!I',header)[0]
                if not 0<length<=PAYLOAD_LIMIT: raise ValueError('Oversized worker frame')
                payload=self.child.stdout.read(length)
                if len(payload)!=length: raise ValueError('Truncated worker frame')
                value=json.loads(payload)
                if not isinstance(value,dict) or value.get('kind') not in {'stage','candidate','result'}:
                    raise ValueError('Unexpected worker message')
                if value['kind']!='candidate' and length>METADATA_LIMIT: raise ValueError('Oversized worker metadata')
                self._offer(value)
            self._offer({'kind':'eof'})
        except (ValueError,UnicodeError,OSError):
            self._offer({'kind':'malformed'})

    def _stderr(self):
        while chunk:=self.child.stderr.read(4096):
            self.errors.extend(chunk)
            if len(self.errors)>16384: del self.errors[:-16384]

    def stop(self):
        self.cancel.touch(exist_ok=True)

    def signal_kill(self):
        try: os.killpg(self.child.pid,signal.SIGKILL)
        except (ProcessLookupError,PermissionError): pass

    def reap(self,deadline):
        try: self.child.wait(timeout=max(0,deadline-time.monotonic()))
        except subprocess.TimeoutExpired: return
        # With subreaper enabled, native descendants belong to this parent.
        while time.monotonic()<deadline:
            try:
                pid,_=os.waitpid(-self.child.pid,os.WNOHANG)
                if pid==0: time.sleep(.01);continue
            except ChildProcessError: break

    def kill(self):
        self.signal_kill()
        self.reap(time.monotonic()+1)

    def close(self):
        self.closed.set()
        self.kill()
        self.reader.join(timeout=1);self.stderr_reader.join(timeout=1)
        self.child.stdout.close();self.child.stderr.close();self.input.close()
        self.directory.cleanup()


@dataclass
class Run:
    claim: dict
    process: object
    stop_deadline: float | None = None
    interrupted: bool = False
    fenced: bool = False
    started: float = field(default_factory=time.monotonic)
    finished: Event = field(default_factory=Event)


class JobController:
    def __init__(self,store,workers=None,clock=None,process_factory=None):
        self.store=store
        self.workers=worker_allocation(workers)
        self.store.worker_limit=self.workers
        self.clock=clock or (lambda:datetime.now(timezone.utc))
        self.process_factory=process_factory or ComputeProcess
        self.accepting=False
        self.runs={}
        self.pending_terminal={}
        self.lock=Lock()
        self.closing=Event()
        self.thread=None
        self.shutdown_deadline=None
        self.shutdown_started=None
        # Allow reaping owned native grandchildren after a group kill on Linux.
        if sys.platform=='linux':
            try: ctypes.CDLL(None).prctl(36,1,0,0,0)
            except (AttributeError,OSError): pass

    def check_consistency(self):
        return self.store.check_consistency()

    def recover_startup(self):
        self.close_admission()
        return self.store.recover_startup(self.clock())

    def start(self):
        if self.thread is not None: return
        if not self.store.generation: self.recover_startup()
        self.accepting=True
        self.thread=Thread(target=self._dispatch,daemon=True);self.thread.start()
        self.watchdog=Thread(target=self._watch,daemon=True);self.watchdog.start()

    def close_admission(self):
        with self.lock:
            self.accepting=False

    def signal_stop(self,identity,requested_at=None):
        with self.lock:
            run=self.runs.get(identity)
            if run is None: return
            remaining=10 if requested_at is None else max(0,10-(self.clock()-requested_at).total_seconds())
            deadline=time.monotonic()+remaining
            run.stop_deadline=min(run.stop_deadline,deadline) if run.stop_deadline is not None else deadline
            run.process.stop()

    def _watch(self):
        while not self.closing.is_set() or self.runs:
            with self.lock: runs=list(self.runs.values())
            for run in runs:
                if run.stop_deadline is None and time.monotonic()-run.started>run.claim['frozen'].request.time_limit_seconds+2:
                    self.signal_stop(run.claim['job_id'])
                if run.stop_deadline is not None and time.monotonic()>=run.stop_deadline and not run.finished.is_set():
                    run.fenced=True;run.interrupted=True
                    run.process.signal_kill()
            time.sleep(.05)

    def _dispatch(self):
        last_cleanup=0
        while not self.closing.is_set():
            self._reconcile_terminal()
            with self.lock: count=len(self.runs)
            if self.accepting and count<self.workers:
                try:
                    claim=self.store.claim(self.clock())
                    if claim is not None:
                        # Each simultaneous solve receives one native thread.
                        frozen=claim['frozen']
                        with self.lock:
                            if not self.accepting or self.closing.is_set():
                                process=None
                            else:
                                try: process=self.process_factory(frozen,1)
                                except BaseException: process=None
                                if process is not None:
                                    run=Run(claim,process)
                                    self.runs[claim['job_id']]=run
                        if process is None:
                            state='interrupted' if self.closing.is_set() else 'failed'
                            self.store.finish(claim['job_id'],claim['run_id'],claim['generation'],state,{'reason':'shutdown_before_spawn' if state=='interrupted' else 'spawn_failed'},[],self.clock())
                            continue
                        Thread(target=self._control,args=(run,),daemon=True).start()
                        Thread(target=self._consume,args=(run,),daemon=True).start()
                except (StoreUnavailable,JobUnknown,PlanBusy,PlanProblem): pass
            if time.monotonic()-last_cleanup>=3600:
                try: self.store.cleanup(self.clock())
                except (StoreUnavailable,JobUnknown,PlanBusy,PlanProblem): pass
                last_cleanup=time.monotonic()
            self.closing.wait(.1)

    def _reconcile_terminal(self):
        with self.lock: pending=list(self.pending_terminal.items())
        for identity,transition in pending:
            try:
                self.store.finish(*transition)
            except (StoreUnavailable,JobUnknown,PlanBusy): continue
            except PlanProblem: pass  # A durable terminal/generation fence won.
            with self.lock: self.pending_terminal.pop(identity,None)

    def _control(self,run):
        claim=run.claim
        while not run.finished.is_set():
            try:
                control=self.store.control(claim['job_id'],claim['run_id'],claim['generation'],self.clock())
                if not control['owned']:
                    run.fenced=True;run.process.signal_kill();return
                if control.get('stop_requested_at'):
                    self.signal_stop(claim['job_id'],control['stop_requested_at'])
            except (StoreUnavailable,JobUnknown,PlanBusy,PlanProblem): pass
            run.finished.wait(.25)

    def _consume(self,run):
        claim=run.claim
        identity,token,generation=claim['job_id'],claim['run_id'],claim['generation']
        state='failed';result={'reason':'worker_exited_without_result'};manifest=[]
        published=set()
        try:
            while True:
                if run.fenced:
                    state='interrupted';result={'reason':'stop_deadline'};break
                try: event=run.process.events.get(timeout=.1)
                except Empty:
                    continue
                kind=event.get('kind')
                if kind=='candidate':
                    from .solver_models import Candidate
                    candidate=Candidate.model_validate(event['candidate'])
                    if run.fenced: continue
                    # Only a complete acknowledged publication is offered.
                    try: self.store.publish(identity,token,generation,candidate,self.clock())
                    except JobUnknown:
                        # Replaying full SHA-bound content on the owned row is safe.
                        if not run.fenced: self.store.publish(identity,token,generation,candidate,self.clock())
                    from .job_codec import digest
                    published.add(digest(candidate.model_dump(mode='json')))
                elif kind=='result':
                    metadata=event['result']
                    from .solver_models import SolveResult
                    SolveResult.model_validate(metadata|{'candidates':[]})
                    offered=event['manifest']
                    if not isinstance(offered,list) or len(offered)>claim['frozen'].request.max_candidates or any(not isinstance(s,str) or len(s)!=64 for s in offered) or set(offered)-published: raise ValueError('Invalid result manifest')
                    manifest=offered
                    state='stopped' if run.stop_deadline is not None or metadata['status']=='stopped' else 'completed'
                    result=metadata|{'solve_status':metadata['status']}
                    break
                elif kind=='stage':
                    self.store.progress(identity,token,generation,event,self.clock())
                elif kind in {'eof','malformed'}:
                    state='interrupted' if run.stop_deadline is not None else 'failed'
                    result={'reason':'malformed_worker_output' if kind=='malformed' else 'worker_exited_without_result'}
                    break
        except (ValueError,KeyError,TypeError):
            state='failed';result={'reason':'invalid_worker_output'}
        except (StoreUnavailable,JobUnknown,PlanBusy,PlanProblem):
            # Unknown database state stays durable running until recovery.
            return
        finally:
            if run.interrupted: state='interrupted';result={'reason':'stop_deadline'}
            run.process.close()
            transition=(identity,token,generation,state,result,manifest,self.clock())
            try:
                self.store.finish(*transition)
            except ValueError:
                transition=(identity,token,generation,'failed',{'reason':'invalid_worker_output'},[],self.clock())
                try: self.store.finish(*transition)
                except (StoreUnavailable,JobUnknown,PlanBusy):
                    with self.lock: self.pending_terminal[identity]=transition
                except PlanProblem: pass
            except (StoreUnavailable,JobUnknown,PlanBusy):
                with self.lock: self.pending_terminal[identity]=transition
            except PlanProblem: pass
            run.finished.set()
            with self.lock: self.runs.pop(identity,None)

    def begin_shutdown(self,deadline):
        self.close_admission()
        if self.shutdown_deadline is not None:
            self.shutdown_deadline=min(self.shutdown_deadline,deadline)
            return
        self.shutdown_started=time.monotonic()
        self.shutdown_deadline=deadline
        self.closing.set()
        with self.lock: runs=list(self.runs.values())
        for run in runs:
            self.signal_stop(run.claim['job_id'])
            Thread(target=self._record_shutdown,args=(run,),daemon=True).start()

    def shutdown(self,timeout=30):
        self.begin_shutdown(time.monotonic()+min(max(0,timeout),30))
        start=self.shutdown_started;deadline=self.shutdown_deadline
        with self.lock: runs=list(self.runs.values())
        for run in runs:
            run.finished.wait(max(0,min(start+11,deadline)-time.monotonic()))
        with self.lock: remaining=list(self.runs.values())
        for run in remaining:
            run.fenced=True;run.interrupted=True;run.process.signal_kill()
        for run in remaining:
            run.process.reap(deadline)
        if self.thread: self.thread.join(timeout=max(0,min(1,deadline-time.monotonic())))
        return {'stopped':not remaining,'elapsed_seconds':time.monotonic()-start}

    def _record_shutdown(self,run):
        try:
            self.store.request_shutdown(run.claim['job_id'],run.claim['run_id'],run.claim['generation'],self.clock())
        except (StoreUnavailable,JobUnknown,PlanBusy,PlanProblem): pass
