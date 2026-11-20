import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from audiobook.backend import Engine
from audiobook.core import Error,filehash,Store


@pytest.mark.parametrize('stage,termination',[('body','normal'),('headers','normal'),('body','ignore-term')])
def test_total_deadline_ends_periodic_chatter_and_reaps_only_owned_server(tmp_path,stage,termination,monkeypatch):
    portfile=tmp_path/'port';script=Path(__file__).with_name('owned_stream_server.py')
    process=subprocess.Popen([sys.executable,str(script),str(portfile),stage,termination],start_new_session=True)
    sentinel=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True)
    engine=Engine.__new__(Engine);engine.process=process;engine.store=SimpleNamespace(root=tmp_path);engine.identity={'settings':{}}
    (tmp_path/'runtime').mkdir();(tmp_path/'runtime/backend.log').write_text('backend: Vulkan0\n')
    monkeypatch.setattr(Engine,'REQUEST_SECONDS',.18,raising=False);monkeypatch.setattr(Engine,'TERMINATE_SECONDS',.05,raising=False)
    try:
        start=time.monotonic()
        while not portfile.exists():
            assert time.monotonic()-start<5;time.sleep(.01)
        engine.client=httpx.Client(base_url='http://127.0.0.1:'+portfile.read_text(),timeout=.08,trust_env=False)
        started=time.monotonic()
        with pytest.raises(Error) as error:engine.speech('An ordinary canary passage.')
        assert error.value.code=='speech_deadline'
        assert time.monotonic()-started<1
        assert process.poll() is not None and sentinel.poll() is None
    finally:
        engine.__exit__(None,None,None)
        if sentinel.poll() is None:sentinel.terminate()
        sentinel.wait(timeout=5)


@pytest.mark.parametrize('value',['x'*6000,'界'*400])
def test_single_huge_token_is_rejected_before_transport(value):
    engine=Engine.__new__(Engine)
    with pytest.raises(Error) as error:engine.speech(value)
    assert error.value.code=='speech_input_limit'


def test_startup_health_body_has_its_own_total_budget(tmp_path,monkeypatch):
    script=Path(__file__).with_name('owned_stream_server.py');binary=tmp_path/'fake-backend';model=tmp_path/'model';model.write_text('CPU-only fault control')
    binary.write_text('#!'+sys.executable+'\nimport os,sys\nos.execv(sys.executable,[sys.executable,'+repr(str(script))+','+repr(str(tmp_path/'port'))+',"body","normal",*sys.argv[1:]])\n');binary.chmod(0o755)
    identity={'binary':str(binary),'binary_sha256':filehash(binary),'model':str(model),'model_sha256':filehash(model),'settings':{}}
    monkeypatch.setattr(Engine,'STARTUP_SECONDS',.35);monkeypatch.setattr(Engine,'TERMINATE_SECONDS',.05)
    engine=Engine(Store(tmp_path/'state'),identity);started=time.monotonic()
    with pytest.raises(Error) as error:engine.__enter__()
    assert error.value.code=='backend_timeout' and time.monotonic()-started<1.5
    assert engine.process.poll() is not None and engine.log.closed


def test_dead_worker_is_not_retried_and_deadline_diagnostic_survives():
    from audiobook.jobs import generate_takes
    calls=[]
    class Dead:
        def speech(self,*args,**kwargs):calls.append('request');raise Error('speech_deadline','Deadline reached.')
        def __exit__(self,*args):calls.append('stop')
    job={'voice':{},'clips':{}}
    with pytest.raises(Error) as error:list(generate_takes(Dead(),job,[({'id':'u1','text':'A canary.'},'k1')],1))
    assert error.value.code=='speech_deadline' and calls==['request','stop']
    assert job['failed_take']['utterance']=='u1'
