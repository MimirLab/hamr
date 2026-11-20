"""Finite CPU-only transport fault fixture, never a TTS/runtime substitute."""
import http.server
from pathlib import Path
import signal
import sys
import time

if sys.argv[3]=='ignore-term':signal.signal(signal.SIGTERM,signal.SIG_IGN)
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.do_POST()
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length',0)))
        try:
            if sys.argv[2]=='headers':
                for byte in b'HTTP/1.0 200 OK\r\nX-Research-Control: '+b'x'*100:
                    self.wfile.write(bytes([byte]));self.wfile.flush();time.sleep(.01)
                return
            self.send_response(200);self.send_header('X-Sample-Rate','24000');self.send_header('X-Sample-Format','s16le');self.end_headers()
            for _ in range(100):
                self.wfile.write(b'\x10\x20\xf0\xdf'*120);self.wfile.flush();time.sleep(.01)
        except (BrokenPipeError,ConnectionResetError):pass
    def log_message(self,*args):pass
port=int(sys.argv[sys.argv.index('--port')+1]) if '--port' in sys.argv else 0
server=http.server.HTTPServer(('127.0.0.1',port),Handler)
Path(sys.argv[1]).write_text(str(server.server_address[1]))
server.serve_forever()
