"""Exercise the optional reviewer through the real loopback and job entry points."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest

from audiobook.cli import main
from audiobook.core import Error
from audiobook.importers import markdown
from audiobook.planner import plan


@contextmanager
def serve(respond):
    requests=[]

    class Endpoint(BaseHTTPRequestHandler):
        def do_POST(self):
            data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(data)
            body=json.dumps(respond(data['requests'])).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    with ThreadingHTTPServer(('127.0.0.1',0),Endpoint) as server:
        thread=threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}/review',requests
        finally:
            server.shutdown()
            thread.join()


def choose_date(request, order):
    choice=next(candidate for candidate in request['candidates'] if candidate['fields'].get('date_order')==order)
    return {'source_digest':request['source_digest'],'occurrence_id':request['occurrence_id'],
            'action':'select','candidate_id':choice['id'],'evidence_ids':choice['evidence_ids'],'requested_context':[]}


def test_real_endpoint_selects_a_known_reading_and_replays_without_network(tmp_path,capsys):
    source=tmp_path/'book.md'
    source.write_text('The hearing is on 03/04.\n',encoding='utf-8')
    home=tmp_path/'state'
    with serve(lambda req:[choose_date(req[0],'DM')]) as (endpoint,requests):
        assert main(['--home',str(home),'queue',str(source),'--context-review-endpoint',endpoint,'--json'])==0
        job=json.loads(capsys.readouterr().out)['job']
        request,=requests
        assert request['schema_version']==1 and len(request['requests'])==1
        assert str(source) not in json.dumps(request)
    assert main(['--home',str(home),'show',job,'--content','--json'])==0
    stored=json.loads(capsys.readouterr().out)['plan']
    assert stored['contextual_resolution']['review_provider']['outcome']=='selected'
    assert endpoint not in json.dumps(stored)
    assert 'April third' in stored['utterances'][0]['text']
    replay=plan(markdown(source.read_bytes()),stored['policy'])
    assert json.loads(json.dumps(replay))==stored


def test_malformed_provider_reply_and_unavailability_keep_code_fallback():
    document=markdown(b'The hearing is on 03/04.')
    fallback=plan(document)
    with serve(lambda req:[{**choose_date(req[0],'DM'),'candidate_id':'invented'}]) as (endpoint,requests):
        result=plan(document,context_reviewer=endpoint)
        assert len(requests)==1
        assert result['utterances']==fallback['utterances'] and not result['issues']
        assert result['contextual_resolution']['review_provider']['outcome']=='invalid_reply'
        assert plan(document,result['policy'])==result
        assert plan(markdown(b'The specimen weighs 5 kg.'),context_reviewer=endpoint)['contextual_resolution'].get('review_provider') is None
        assert len(requests)==1
    result=plan(document,context_reviewer=endpoint)
    assert result['utterances']==fallback['utterances']
    assert result['contextual_resolution']['review_provider']['outcome']=='unavailable'
    strict=plan(document,{'context':{'mode':'strict'}},context_reviewer=endpoint)
    assert strict['issues']
    assert strict['contextual_resolution']['review_provider']['outcome']=='unavailable'


@pytest.mark.parametrize('endpoint',[
    'https://127.0.0.1:8000/review','http://localhost:8000/review','http://example.org:8000/review',
    'http://127.0.0.1/review','http://127.0.0.1:8000/review?token=x','http://secret@127.0.0.1:8000/review',
])
def test_provider_address_is_an_explicit_loopback_http_port(endpoint):
    with pytest.raises(Error,match='loopback'):
        plan(markdown(b'The hearing is on 03/04.'),context_reviewer=endpoint)
