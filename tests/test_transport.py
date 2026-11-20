import threading,http.server,socketserver,json
import httpx,pytest
from audiobook.backend import Engine,acquire
from audiobook.core import Error,digest


def test_truncated_chunked_stream_rejected(tmp_path):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200); self.send_header('Transfer-Encoding','chunked'); self.send_header('X-Sample-Rate','24000'); self.send_header('X-Sample-Format','s16le'); self.end_headers()
            self.wfile.write(b'4\r\n1234\r\n'); self.wfile.flush(); self.close_connection=True
        def log_message(self,*args): pass
    with socketserver.TCPServer(('127.0.0.1',0),Handler) as srv:
        thread=threading.Thread(target=srv.serve_forever,daemon=True); thread.start()
        engine=Engine.__new__(Engine); engine.client=httpx.Client(base_url=f'http://127.0.0.1:{srv.server_address[1]}',trust_env=False)
        try:
            with pytest.raises(Error,match='finish'): engine.speech('A valid utterance.')
        finally: engine.client.close(); srv.shutdown(); thread.join()


def test_interrupted_download_resumes_only_bound_identity(tmp_path):
    content=b'bound artifact payload'*10000; seen=[]
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            offset=int(self.headers.get('Range','bytes=0-').split('=')[1].split('-')[0]); seen.append(offset)
            self.send_response(206 if offset else 200)
            if offset: self.send_header('Content-Range',f'bytes {offset}-{len(content)-1}/{len(content)}')
            self.send_header('Content-Length',str(len(content)-offset)); self.end_headers(); self.wfile.write(content[offset:])
        def log_message(self,*args): pass
    with socketserver.TCPServer(('127.0.0.1',0),Handler) as srv:
        thread=threading.Thread(target=srv.serve_forever,daemon=True); thread.start(); url=f'http://127.0.0.1:{srv.server_address[1]}/artifact'; dest=tmp_path/'model'; part=tmp_path/'model.part'
        part.write_bytes(content[:123]); (tmp_path/'model.part.json').write_text(json.dumps({'url':url,'sha256':digest(content),'bytes':len(content)}))
        try: acquire(url,dest,digest(content),len(content)); assert dest.read_bytes()==content and seen==[123]
        finally: srv.shutdown(); thread.join()

@pytest.mark.parametrize('parallel',[0,5,True,'3'])
def test_adapter_rejects_unbounded_parallel_before_startup(parallel):
    engine=Engine.__new__(Engine);engine.identity={'settings':{'parallel':parallel}}
    with pytest.raises(Error,match='one to four'):engine.__enter__()

def test_adapter_rejects_concurrent_cpu_text_encoder():
    engine=Engine.__new__(Engine);engine.identity={'settings':{'parallel':3,'text_encoder':'cpu'}}
    with pytest.raises(Error,match='not qualified'):engine.__enter__()
