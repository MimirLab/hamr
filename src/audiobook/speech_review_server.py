"""Serve one saved job's review over loopback until the operator closes it."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import re
import secrets

from . import qa, speech_review
from .core import Error, Store, dumps


def server(store, jid):
    store.get(jid)
    root = store.root
    prefix = '/' + secrets.token_urlsafe(24) + '/'

    class ReviewRequest(BaseHTTPRequestHandler):
        def handle(self):
            self.connection.settimeout(10)
            self.store = Store(root)
            try:
                super().handle()
            finally:
                self.store.db.close()

        def log_message(self, *args):
            pass

        def reply(self, status, data, media='application/json; charset=utf-8', **headers):
            self.send_response(status)
            self.send_header('Content-Type', media)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy',
                             "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

        def json(self, status, value):
            self.reply(status, dumps(value).encode('utf-8'))

        def route(self):
            if self.headers.get('Host') != self.server.address or not self.path.startswith(prefix):
                raise Error('review_address', 'Open the loopback URL printed by Hamr.')
            return self.path[len(prefix):]

        def do_GET(self):
            try:
                route = self.route()
                assets = {'': ('html', 'text/html'), 'review.js': ('js', 'text/javascript'),
                          'review.css': ('css', 'text/css')}
                if route in assets:
                    extension, media = assets[route]
                    asset = Path(__file__).with_name('speech_review.' + extension)
                    self.reply(200, asset.read_bytes(), media + '; charset=utf-8')
                    return
                job = self.store.get(jid)
                if route == 'data':
                    self.json(200, speech_review.snapshot(self.store, job))
                elif route == 'report.md':
                    data = speech_review.snapshot(self.store, job)
                    self.reply(200, speech_review.markdown(data).encode('utf-8'), 'text/markdown; charset=utf-8',
                               **{'Content-Disposition': f'attachment; filename="hamr-review-{jid}.md"'})
                elif match := re.fullmatch(r'audio/([a-f0-9]{64})/([a-f0-9]{64})\.wav', route):
                    clip, pcm = match.groups()
                    rows = ('audio_review', 'audio_acceptances', 'revoked_audio_acceptances', 'listening_findings')
                    if not any((row['clip_key'], row.get('pcm_sha256')) == (clip, pcm)
                               for field in rows for row in job.get(field, [])):
                        raise Error('review_audio', 'This recording is outside the current job review.')
                    audio = speech_review.read_audio(self.store, clip, pcm)
                    self.reply(200, audio, 'audio/wav')
                else:
                    self.json(404, {'error': 'This review page does not exist.'})
            except Error as exc:
                self.json(409, {'error': str(exc), 'action': exc.action})

        def do_POST(self):
            try:
                if self.route() != 'decision' or self.headers.get('Origin') != 'http://' + self.server.address:
                    raise Error('review_origin', 'Submit decisions from this job’s review page.')
                n = int(self.headers.get('Content-Length', '0'))
                if not 0 < n <= 8192 or self.headers.get_content_type() != 'application/json':
                    raise ValueError
                data = json.loads(self.rfile.read(n))
                if not isinstance(data, dict):
                    raise ValueError
                fields = ('action', 'utterance', 'reason', 'clip_key', 'pcm_sha256')
                if any(not isinstance(data.get(field), str) for field in fields) or type(data.get('revision')) is not int:
                    raise ValueError
                take = data['clip_key'], data['pcm_sha256']
                kwargs = dict(expect_revision=data['revision'], expected_take=take, include_summary=False)
                uid, reason = data['utterance'], data['reason']
                if data['action'] == 'accept':
                    qa.accept(self.store, jid, uid, reason, **kwargs)
                elif data['action'] in ('recording', 'planned_reading'):
                    qa.flag(self.store, jid, uid, reason, kind=data['action'], **kwargs)
                else:
                    raise ValueError
                self.json(200, speech_review.snapshot(self.store, self.store.get(jid)))
            except (ValueError, UnicodeError):
                self.json(400, {'error': 'A valid decision, exact recording and reason are required.'})
            except Error as exc:
                self.json(409, {'error': str(exc), 'action': exc.action})

    class ReviewServer(ThreadingHTTPServer):
        def handle_error(self, request, address):
            # Browser cancellation of an audio request is routine.
            import sys
            if not isinstance(sys.exception(), (BrokenPipeError, ConnectionResetError)):
                super().handle_error(request, address)

    httpd = ReviewServer(('127.0.0.1', 0), ReviewRequest)
    httpd.address = f'127.0.0.1:{httpd.server_port}'
    return httpd, 'http://' + httpd.address + prefix


def serve(store, jid):
    httpd, url = server(store, jid)
    with httpd:
        print(f'Open this local review in your browser:\n{url}\nPress Ctrl-C to close it.', flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
