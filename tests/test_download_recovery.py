import contextlib
import http.server
import json
import os
import subprocess
import sys
import threading
import time
import pytest
from audiobook import backend
from audiobook.core import Error,digest


@contextlib.contextmanager
def server(handler):
    with http.server.ThreadingHTTPServer(('127.0.0.1',0),handler) as service:
        thread=threading.Thread(target=lambda:service.serve_forever(poll_interval=.02),daemon=True);thread.start()
        try:yield f'http://127.0.0.1:{service.server_address[1]}/asset'
        finally:service.shutdown();thread.join()


class Quiet(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args):pass


@pytest.mark.parametrize('mode,code',[('body_stall','download_stalled'),('header_chatter','download_stalled'),('body_chatter','download_timeout')])
def test_watchdog_reaps_transfer_without_touching_other_workers(tmp_path,monkeypatch,mode,code):
    stop=threading.Event();content=b'z'*500
    class Handler(Quiet):
        def do_GET(self):
            try:
                self.send_response(200);self.send_header('Content-Length',str(len(content)))
                if mode=='header_chatter':
                    self.flush_headers()
                    while not stop.wait(.025):self.wfile.write(b'X-Chatter: alive\r\n');self.wfile.flush()
                    return
                self.end_headers();self.wfile.write(content[:1]);self.wfile.flush()
                while not stop.wait(.025):
                    if mode=='body_chatter':self.wfile.write(b'z');self.wfile.flush()
            except (BrokenPipeError,ConnectionResetError):pass
    monkeypatch.setattr(backend,'DOWNLOAD_STALL_SECONDS',.22);monkeypatch.setattr(backend,'DOWNLOAD_TOTAL_SECONDS',.55);monkeypatch.setattr(backend,'DOWNLOAD_POLL_SECONDS',.01)
    sentinel=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
    try:
        with server(Handler) as url:
            start=time.monotonic()
            with pytest.raises(Error) as failure:backend.acquire(url,tmp_path/'model',digest(content),len(content))
            assert failure.value.code==code and time.monotonic()-start<3
            assert sentinel.poll() is None and not (tmp_path/'model').exists()
            binding=json.loads((tmp_path/'model.part.json').read_text());assert binding['sha256']==digest(content)
            if mode!='header_chatter':assert 0<(tmp_path/'model.part').stat().st_size<len(content)
    finally:stop.set();sentinel.terminate();sentinel.wait()


@pytest.mark.parametrize('mode',['full_fallback','wrong_start','wrong_total','wrong_length'])
def test_exact_range_identity_and_full_response_fallback(tmp_path,mode):
    content=b'correct pinned asset'*100;dest=tmp_path/'model';partial=tmp_path/'model.part'
    class Handler(Quiet):
        def do_GET(self):
            assert self.headers['Range']=='bytes=7-'
            self.send_response(200 if mode=='full_fallback' else 206)
            if mode!='full_fallback':self.send_header('Content-Range',f'bytes {8 if mode=="wrong_start" else 7}-{len(content)-1}/{len(content)+1 if mode=="wrong_total" else len(content)}')
            body=content if mode=='full_fallback' else content[7:]
            self.send_header('Content-Length',str(len(body)+1 if mode=='wrong_length' else len(body)));self.end_headers()
            self.wfile.write(body)
    with server(Handler) as url:
        partial.write_bytes(content[:7]);(tmp_path/'model.part.json').write_text(json.dumps({'url':url,'sha256':digest(content),'bytes':len(content)}))
        if mode=='full_fallback':
            backend.acquire(url,dest,digest(content),len(content));assert dest.read_bytes()==content and not partial.exists()
        else:
            with pytest.raises(Error) as failure:backend.acquire(url,dest,digest(content),len(content))
            assert failure.value.code=='download_identity' and partial.read_bytes()==content[:7] and not dest.exists()


def test_partial_hardlink_cannot_truncate_an_unrelated_file(tmp_path):
    sentinel=tmp_path/'sentinel';sentinel.write_bytes(b'preserve this byte identity');before=sentinel.read_bytes()
    os.link(sentinel,tmp_path/'model.part')
    with pytest.raises(Error) as failure:backend.acquire('https://example.invalid/asset',tmp_path/'model','0'*64,100)
    assert failure.value.code=='unsafe_path' and sentinel.read_bytes()==before
