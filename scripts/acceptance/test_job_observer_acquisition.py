"""Actual observer boundary, disposable blocked child; never a solver or job."""
from pathlib import Path
from types import SimpleNamespace
import json
import subprocess
import sys
import pytest
from scripts.acceptance.job_lifecycle_observer import JobLifecycleObserver, identity


def test_observer_emission_failure_reaps_just_acquired_process(tmp_path,monkeypatch):
    child=subprocess.Popen([sys.executable,'-c','import sys;sys.stdin.read()'],stdin=subprocess.PIPE,start_new_session=True)
    started=identity(child.pid);closed=[];calls=[]
    def close():
        closed.append(True)
        if child.poll() is None:child.terminate()
        child.wait(timeout=3)
    process=SimpleNamespace(child=child,close=close)
    frozen=SimpleNamespace(source_revision=1,input_version=1,latest_adoption=1,model_dump=lambda **kw:{'local_fixture':True})
    def construct(value,threads):calls.append((value,threads));return process
    observer=JobLifecycleObserver(tmp_path/'observer.ndjson',construct)
    observer.claim({'job_id':'LOCAL','run_id':'R','generation':'G','frozen':frozen})
    failure=OSError('LOCAL_LEDGER_PUBLICATION_FAILURE')
    def failed_emit(value):raise failure
    monkeypatch.setattr(observer,'emit',failed_emit)
    try:
        with pytest.raises(OSError) as caught:observer.process(frozen,1)
        assert caught.value is failure and calls==[(frozen,1)]
        assert child.poll() is not None,'Observer lost its just-acquired process when ledger emission failed.'
        assert identity(child.pid) is None
    finally:
        # Separate test cleanup preserves the resource boundary on the RED.
        separate_cleanup = child.poll() is None
        if separate_cleanup:close()
        proof={'pid':child.pid,'start':started['start'],'pgid':started['pgid'],'returncode':child.poll(),'actual_identity':identity(child.pid),'separate_test_cleanup_called':separate_cleanup}
        (tmp_path/'factory-error-cleanup.json').write_text(json.dumps(proof,indent=2)+'\n')


@pytest.mark.parametrize('boundary', ['payload','identity','registry'])
def test_post_factory_metadata_failures_release_only_acquired_handle(tmp_path,monkeypatch,boundary):
    import scripts.acceptance.job_lifecycle_observer as module
    child=subprocess.Popen([sys.executable,'-c','import sys;sys.stdin.read()'],stdin=subprocess.PIPE,start_new_session=True)
    started=identity(child.pid)
    calls=[]
    def close():
        calls.append('close')
        if child.poll() is None:child.terminate()
        child.wait(timeout=3)
    process=SimpleNamespace(child=child,close=close)
    frozen=SimpleNamespace(source_revision=1,input_version=1,latest_adoption=1,model_dump=lambda **kw:{'local_fixture':True})
    observer=JobLifecycleObserver(tmp_path/'observer.ndjson',lambda value,threads:process)
    observer.claim({'job_id':'LOCAL','run_id':'R','generation':'G','frozen':frozen})
    failure=ValueError('LOCAL_POST_FACTORY_'+boundary)
    def fail(*args,**kwargs):raise failure
    if boundary=='payload':frozen.model_dump=fail
    if boundary=='identity':monkeypatch.setattr(module,'identity',fail)
    if boundary=='registry':
        class Registry(dict):
            def __setitem__(self,key,value):raise failure
        observer.processes=Registry()
    try:
        with pytest.raises(ValueError) as caught:observer.process(frozen,1)
        assert caught.value is failure and calls==['close']
        assert child.poll() is not None and identity(child.pid) is None
        assert observer.failed_acquisitions[0][0] is process
        assert observer.failed_acquisitions[0][1]['close_returned'] is True
        assert observer.failed_acquisitions[0][1]['ownership_closed'] is None
    finally:
        separate_cleanup = child.poll() is None
        if separate_cleanup:close()
        (tmp_path/'metadata-cleanup.json').write_text(json.dumps({'pid':child.pid,**started,'returncode':child.poll(),'identity':identity(child.pid),'separate_test_rescue':separate_cleanup})+'\n')


def test_close_failure_preserves_primary_and_retains_unknown_acquisition(tmp_path,monkeypatch):
    from scripts.acceptance.native_quantity_case import collect_job_cleanup
    child=subprocess.Popen([sys.executable,'-c','import sys;sys.stdin.read()'],stdin=subprocess.PIPE,start_new_session=True)
    started=identity(child.pid)
    closed=[];primary=ValueError('LOCAL_PRIMARY');secondary=OSError('LOCAL_CLOSE_FAILED')
    def close():
        closed.append(True)
        raise secondary
    process=SimpleNamespace(child=child,close=close)
    frozen=SimpleNamespace(source_revision=1,input_version=1,latest_adoption=1,model_dump=lambda **kw:{'local_fixture':True})
    observer=JobLifecycleObserver(tmp_path/'observer.ndjson',lambda value,threads:process)
    observer.claim({'job_id':'LOCAL','run_id':'R','generation':'G','frozen':frozen})
    def emit(value):raise primary
    monkeypatch.setattr(observer,'emit',emit)
    try:
        with pytest.raises(ValueError) as caught:observer.process(frozen,1)
        assert caught.value is primary and closed==[True] and child.poll() is None
        handle,note=observer.failed_acquisitions[0]
        assert handle is process and note['close_error_class']=='OSError' and note['close_returned'] is False and note['ownership_closed'] is None
        _,cleanup=collect_job_cleanup(observer)
        assert cleanup['job_acquired'] is True and cleanup['all_closed'] is None and cleanup['acquisition_failures']==[note]
    finally:
        if child.poll() is None:child.terminate()
        child.wait(timeout=3)
        (tmp_path/'separate-rescue.json').write_text(json.dumps({'pid':child.pid,**started,'returncode':child.poll(),'identity':identity(child.pid),'separate_test_rescue':True})+'\n')


def test_factory_failure_never_closes_an_unacquired_or_existing_handle(tmp_path):
    closed=[];failure=RuntimeError('LOCAL_FACTORY_FAILED')
    def construct(*args):raise failure
    observer=JobLifecycleObserver(tmp_path/'observer.ndjson',construct)
    observer.processes['other']=(SimpleNamespace(close=lambda:closed.append('unrelated')),{'job_id':'other'})
    frozen=SimpleNamespace()
    observer.claim({'job_id':'LOCAL','run_id':'R','generation':'G','frozen':frozen})
    with pytest.raises(RuntimeError) as caught:observer.process(frozen,1)
    assert caught.value is failure and closed==[] and observer.failed_acquisitions==[]
