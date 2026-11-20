from types import SimpleNamespace
import httpx
import numpy as np
import pytest
from audiobook.backend import Engine
from audiobook.core import Error
from audiobook.jobs import generate_takes


@pytest.mark.parametrize('new_error',[False,True])
def test_clean_http_eof_does_not_hide_generation_error_and_old_errors_do_not_poison_new_request(tmp_path,new_error):
    runtime=tmp_path/'runtime';runtime.mkdir();log=runtime/'backend.log'
    log.write_text('backend: Vulkan0\ngeneration error: older completed attempt\n')
    pcm=(np.sin(np.arange(24000)*2*np.pi*440/24000)*10000).astype('<i2').tobytes()
    def handler(request):
        with log.open('a') as f:f.write(('generation error: synthetic late worker failure\n' if new_error else '')+'100% 12 frames in 3 flushes\n')
        return httpx.Response(200,headers={'X-Sample-Rate':'24000','X-Sample-Format':'s16le'},content=pcm)
    engine=Engine.__new__(Engine);engine.store=SimpleNamespace(root=tmp_path);engine.identity={'settings':{}};engine.process=None;engine.client=httpx.Client(base_url='http://owned-control.invalid',transport=httpx.MockTransport(handler))
    try:
        if new_error:
            with pytest.raises(Error) as exc:engine.speech('A complete canary sentence.')
            assert exc.value.code=='generation_failed'
        else:
            data,receipt=engine.speech('A complete canary sentence.')
            assert data==pcm and receipt['actual_device']=='Vulkan0'
    finally:engine.client.close()


def test_worker_generation_failure_is_not_replayed_in_a_dead_engine():
    calls=[]
    class Failed:
        def speech(self,*args,**kwargs):calls.append('request');raise Error('generation_failed','Synthetic failure.')
        def __exit__(self,*args):calls.append('stop')
    with pytest.raises(Error):list(generate_takes(Failed(),{'voice':{}},[({'id':'u1','text':'A canary.'},'key')],1))
    assert calls==['request','stop']
